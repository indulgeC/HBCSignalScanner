"""
LLM enrichment — calls the Anthropic API to generate:
  - Signal Title
  - Strategic Notes
  - Estimated Value selection (when multiple amounts found)
  - Signal Strength refinement
  - Strategic Fit refinement
"""

from __future__ import annotations
import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy-load client to avoid import errors when API key not set
_client = None
_client_api_key = None


DEFAULT_MODEL = "claude-sonnet-4-6"


def _get_client(api_key: str = ""):
    global _client, _client_api_key
    # Recreate client if api_key changed
    if _client is None or (api_key and api_key != _client_api_key):
        try:
            import anthropic
            if api_key:
                _client = anthropic.Anthropic(api_key=api_key)
            else:
                _client = anthropic.Anthropic()
            _client_api_key = api_key
        except Exception as e:
            logger.warning("Anthropic client not available: %s", e)
            return None
    return _client


def validate_credentials(api_key: str = "", model: str = "") -> tuple[bool, str]:
    """Test the API key + model with a minimal call.

    Returns (ok, error_message). Used by the pipeline before processing
    so that a bad key surfaces immediately instead of silently degrading
    every signal to rule-only enrichment.
    """
    client = _get_client(api_key)
    if client is None:
        return False, "Anthropic SDK not installed or API key missing."
    try:
        client.messages.create(
            model=(model or DEFAULT_MODEL),
            max_tokens=1,
            messages=[{"role": "user", "content": "ok"}],
        )
        return True, ""
    except Exception as e:
        return False, str(e)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN ENRICHMENT FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

def enrich_signal(
    text: str,
    agency: str,
    sector: str,
    procurement_stage: str,
    signal_type: str,
    amounts: list[dict],
    rule_strength: str,
    rule_fit: str,
    api_key: str = "",
    model: str = "",
) -> dict:
    """
    Call LLM to generate title, notes, and refine assessments.
    Returns dict with keys: signal_title, strategic_notes,
    estimated_value, signal_strength, strategic_fit.
    Falls back to rule-based defaults if API unavailable.
    """
    client = _get_client(api_key)
    if client is None:
        return _fallback(text, agency, sector, procurement_stage, amounts, rule_strength, rule_fit)

    use_model = model or DEFAULT_MODEL

    amounts_str = ""
    if amounts:
        amounts_str = "Amounts found in text:\n" + "\n".join(
            f"  - {a['raw']} (context: {a['context'][:100]})" for a in amounts
        )

    prompt = f"""You are a government infrastructure signal analyst. Given the following excerpt from a government document, produce a structured analysis.

SECTOR FOCUS: {sector}
AGENCY: {agency}
PROCUREMENT STAGE (rule-detected): {procurement_stage or 'unknown'}
SIGNAL TYPE (rule-detected): {signal_type or 'unknown'}
SIGNAL STRENGTH (rule-detected): {rule_strength or 'unknown'}
{amounts_str}

--- DOCUMENT EXCERPT ---
{text[:3000]}
--- END EXCERPT ---

Respond ONLY with a JSON object (no markdown fences) with these keys:

1. "signal_title": A concise title in format "[Agency] + [Core Action] + [Asset/Project]". Max 80 chars.

2. "strategic_notes": Exactly 2 sentences:
   - Sentence 1: What triggered this signal.
   - Sentence 2: Why it matters and what stage comes next.

3. "estimated_value": If amounts were found, pick the one most likely to be the project/contract value. If uncertain or no amounts, return empty string.

4. "signal_strength": "High", "Medium", or "Low". High = specific project + action + identifiers. Medium = clear topic but action not finalized. Low = general mention only.

5. "strategic_fit": "Strong Fit", "Moderate Fit", "Monitor", or "No Fit" for a {sector} infrastructure contractor.

Return ONLY the JSON object."""

    try:
        response = client.messages.create(
            model=use_model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        return {
            "signal_title": str(data.get("signal_title", ""))[:120],
            "strategic_notes": str(data.get("strategic_notes", ""))[:500],
            "estimated_value": str(data.get("estimated_value", "")),
            "signal_strength": str(data.get("signal_strength", rule_strength)),
            "strategic_fit": str(data.get("strategic_fit", rule_fit)),
        }
    except Exception as e:
        logger.warning("LLM enrichment failed: %s", e)
        return _fallback(text, agency, sector, procurement_stage, amounts, rule_strength, rule_fit)


# ══════════════════════════════════════════════════════════════════════════════
#  FALLBACK (no API)
# ══════════════════════════════════════════════════════════════════════════════

def _fallback(
    text: str,
    agency: str,
    sector: str,
    procurement_stage: str,
    amounts: list[dict],
    rule_strength: str,
    rule_fit: str,
) -> dict:
    """Generate fields without LLM — basic heuristic."""
    # Title: agency + first meaningful line
    first_line = text.strip().split("\n")[0][:100]
    title = f"{agency} — {first_line}" if agency else first_line

    # Notes
    notes = f"Signal detected in {sector}-related content."
    if procurement_stage:
        notes += f" Currently at {procurement_stage} stage."

    # Value
    from classifiers.rules import select_best_amount
    value = select_best_amount(amounts) if amounts else ""

    return {
        "signal_title": title[:120],
        "strategic_notes": notes[:500],
        "estimated_value": value,
        "signal_strength": rule_strength,
        "strategic_fit": rule_fit,
    }
