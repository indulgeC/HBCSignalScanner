"""
Rule-based classifiers — deterministic extraction for:
  Procurement Stage, Signal Type, Lifecycle Stage,
  Friction Level, Estimated Value, Expected Timeline,
  Signal Strength, Strategic Fit, Trigger Event, Meeting Date.
"""

from __future__ import annotations
import re
from typing import Optional

# ══════════════════════════════════════════════════════════════════════════════
#  PROCUREMENT STAGE
# ══════════════════════════════════════════════════════════════════════════════

_STAGE_RULES: list[tuple[str, list[str]]] = [
    ("Active Contract", [
        r"execut\w*\s+(?:a\s+)?contract", r"change order", r"active work", r"extension",
        r"contract\s+(?:modification|amendment)", r"notice to proceed",
        r"work\s+(?:in progress|underway|ongoing)", r"under construction",
        r"active\s+construction", r"currently\s+under",
        r"authoriz\w+.*?execut\w+.*?contract",
    ]),
    ("Construction Authorization", [
        r"authorize\s+construction", r"award\s+construction",
        r"construction\s+(?:contract|award|authorization)",
        r"NTP", r"notice to proceed", r"construction\s+bid\s+award",
    ]),
    ("Design Authorization", [
        r"authorize\s+design", r"design\s+(?:services|contract|authorization|consultant)",
        r"consultant\s+selection", r"professional\s+services",
        r"A/E\s+selection", r"engineering\s+services",
        r"design.{0,20}\d+%", r"\d+%\s*(?:design|complete|completion)",
    ]),
    ("Prequalification (ITB/RFQ set up)", [
        r"\bitb\b", r"\brfq\b", r"\brfp\b",
        r"invitation\s+to\s+bid", r"request\s+for\s+(?:qualifications|proposals)",
        r"solicitation", r"vendor\s+pool", r"prequalif",
        r"bid\s+(?:opening|submission|due)", r"advertis(?:e|ing)\s+(?:for|the)",
        r"issuance\s+of\s+(?:itb|rfq|rfp)",
    ]),
    ("Funding Allocated", [
        r"appropriat(?:ion|ed)", r"allocated", r"grant\s+award",
        r"budget\s+amendment", r"funding\s+(?:approved|authorized|allocated)",
        r"bond\s+(?:issue|proceeds|authorization)", r"resolution\s+.*approv",
    ]),
    ("Committee Referral", [
        r"refer(?:red)?\s+to\s+.*?committee", r"committee\s+recommendation",
        r"commission\s+referral", r"land\s+use.*committee",
        r"finance\s+committee", r"public\s+works\s+committee",
    ]),
    ("Concept/Discussion", [
        r"discuss(?:ion)?", r"presentation", r"workshop",
        r"update", r"briefing", r"information\s+item",
        r"study", r"feasibility", r"assessment", r"evaluation",
        r"planning\s+(?:phase|study|effort)",
    ]),
]


def classify_procurement_stage(text: str) -> tuple[str, str]:
    """
    Returns (stage, matched_evidence).
    Strategy: collect all matches, then pick the most advanced concrete stage.
    """
    text_lower = text.lower()

    # ── Collect all matches with context ─────────────────────────
    _FUTURE_CONTEXT = re.compile(
        r"(?:anticipat|expect|plan(?:ned)?|prepar|upcoming|propos|future|forecast|pending|request(?:ing)?(?:\s+direction))",
        re.I,
    )

    candidates: list[tuple[str, str, bool, int]] = []  # (stage, evidence, is_future, stage_idx)

    for stage_idx, (stage, patterns) in enumerate(_STAGE_RULES):
        for pat in patterns:
            m = re.search(pat, text_lower)
            if m:
                start = max(0, m.start() - 60)
                end = min(len(text), m.end() + 60)
                context = text[start:end].strip()
                is_future = bool(_FUTURE_CONTEXT.search(context.lower()))
                candidates.append((stage, context, is_future, stage_idx))
                break   # one match per stage is enough

    if not candidates:
        return "", ""

    # Prefer non-future, non-discussion matches (more concrete stages)
    non_future = [(s, e, idx) for s, e, f, idx in candidates if not f]

    if non_future:
        # Among non-future: prefer stages earlier in the list (more advanced),
        # but skip Concept/Discussion if a more specific stage is available
        concrete = [(s, e, idx) for s, e, idx in non_future if s != "Concept/Discussion"]
        if concrete:
            best = min(concrete, key=lambda x: x[2])
            return best[0], best[1]
        # Only Concept/Discussion matched
        return non_future[0][0], non_future[0][1]

    # All matches are future-context; return the most advanced anyway
    best = min(candidates, key=lambda x: x[3])
    return best[0], best[1]


# ══════════════════════════════════════════════════════════════════════════════
#  SIGNAL TYPE
# ══════════════════════════════════════════════════════════════════════════════

_SIGNAL_TYPE_RULES: list[tuple[str, list[str]]] = [
    ("Commission Agenda", [
        r"commission\s+agenda", r"agenda\s+item", r"resolution",
        r"city\s+commission", r"regular\s+meeting",
        r"consent\s+agenda", r"public\s+hearing",
    ]),
    ("Capital Budget", [
        r"capital\s+(?:budget|improvement|program|plan)",
        r"\bcip\b", r"capital\s+project",
        r"five.year\s+(?:capital|cip)",
    ]),
    ("Procurement (ITB/RFQ)", [
        r"\bitb\b", r"\brfq\b", r"\brfp\b",
        r"solicitation", r"procurement",
        r"invitation\s+to\s+bid", r"bid\s+opportunit",
    ]),
    ("Policy Direction", [
        r"ordinance", r"policy\s+memo", r"direction\s+to\s+staff",
        r"legislative", r"code\s+amendment",
    ]),
    ("Funding Allocation", [
        r"grant\s+(?:award|funding)", r"appropriation",
        r"funding\s+(?:amendment|allocation|source)",
        r"bond\s+(?:proceeds|authorization|issue)",
    ]),
]


def classify_signal_type(text: str, category: str = "") -> str:
    """
    Signal Type reflects the SOURCE TYPE of the document/page.
    Category (meetings, procurement, cip, budget) is the strongest indicator.
    Keywords refine when category is absent.
    """
    # Strong category → direct mapping
    cat_map = {
        "meetings": "Commission Agenda",
        "procurement": "Procurement (ITB/RFQ)",
        "budget": "Capital Budget",
        "cip": "Capital Budget",
    }
    if category in cat_map:
        # Check if content strongly indicates a sub-type within this category
        text_lower = text.lower()
        if category == "meetings":
            # A meeting item about funding is still a Commission Agenda item
            # but check for ordinance/policy
            if re.search(r"ordinance|policy\s+memo|code\s+amendment", text_lower):
                return "Policy Direction"
            if re.search(r"appropriation|funding\s+allocat|grant\s+award", text_lower):
                return "Funding Allocation"
        return cat_map[category]

    # No category — fall back to keyword scan
    text_lower = text.lower()
    for sig_type, patterns in _SIGNAL_TYPE_RULES:
        for pat in patterns:
            if re.search(pat, text_lower):
                return sig_type
    return ""


# ══════════════════════════════════════════════════════════════════════════════
#  LIFECYCLE STAGE  (derived from Procurement Stage + Signal Type)
# ══════════════════════════════════════════════════════════════════════════════

_LIFECYCLE_MAP = {
    "Concept/Discussion": "Concept / Policy Direction",
    "Committee Referral": "Concept / Policy Direction",
    "Funding Allocated": "Funding Confirmed",
    "Prequalification (ITB/RFQ set up)": "Procurement Imminent",
    "Design Authorization": "Design Advancement",
    "Construction Authorization": "Procurement Imminent",
    "Active Contract": "Active Contract",
}


def derive_lifecycle(procurement_stage: str, signal_type: str) -> str:
    if procurement_stage in _LIFECYCLE_MAP:
        return _LIFECYCLE_MAP[procurement_stage]
    if signal_type == "Capital Budget":
        return "Budget Inclusion"
    if signal_type == "Funding Allocation":
        return "Funding Confirmed"
    return ""


# ══════════════════════════════════════════════════════════════════════════════
#  EXPECTED TIMELINE (default from stage, override from text)
# ══════════════════════════════════════════════════════════════════════════════

_TIMELINE_DEFAULTS = {
    "Concept/Discussion": "12+ months",
    "Committee Referral": "6-12 months",
    "Funding Allocated": "6-12 months",
    "Prequalification (ITB/RFQ set up)": "3-6 months",
    "Design Authorization": "3-6 months",
    "Construction Authorization": "0-3 months",
    "Active Contract": "0-3 months",
}

_TIMELINE_OVERRIDES = [
    (r"next\s+(\d+)\s+days", lambda m: f"~{m.group(1)} days"),
    (r"(?:due|deadline|closes?)\s+(?:by|on|in)\s+(\d+)\s+days", lambda m: f"~{m.group(1)} days"),
    (r"(?:FY|fiscal\s+year)\s*(\d{4})\s*Q(\d)", lambda m: f"FY{m.group(1)} Q{m.group(2)}"),
    (r"(?:begin|start|commence)s?\s+(?:in\s+)?(?:summer|fall|winter|spring)\s+(\d{4})",
     lambda m: f"{m.group(0).strip()}"),
    (r"responses?\s+due\s+(\w+\s+\d{1,2},?\s+\d{4})", lambda m: f"Due {m.group(1)}"),
    (r"(?:complete|completion)\s+(?:by|date)\s*:?\s*(\w+\s+\d{4})", lambda m: f"Complete {m.group(1)}"),
]


def infer_timeline(text: str, procurement_stage: str) -> str:
    text_lower = text.lower()
    for pat, fmt in _TIMELINE_OVERRIDES:
        m = re.search(pat, text_lower)
        if m:
            try:
                return fmt(m)
            except Exception:
                continue
    return _TIMELINE_DEFAULTS.get(procurement_stage, "")


# ══════════════════════════════════════════════════════════════════════════════
#  ESTIMATED VALUE (rule extraction only — find dollar amounts)
# ══════════════════════════════════════════════════════════════════════════════

_AMOUNT_RE = re.compile(r"\$\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?(?:\s*(?:million|M|billion|B))?", re.I)


def extract_amounts(text: str) -> list[dict]:
    """Find all dollar amounts in text with context."""
    results = []
    for m in _AMOUNT_RE.finditer(text):
        raw = m.group(0)
        value = _parse_amount(raw)
        if value and value >= 1000:   # skip trivially small
            start = max(0, m.start() - 60)
            end = min(len(text), m.end() + 60)
            context = text[start:end].strip()
            results.append({
                "raw": raw,
                "value": value,
                "context": context,
            })
    return results


def _parse_amount(raw: str) -> Optional[float]:
    try:
        s = raw.replace("$", "").replace(",", "").strip()
        multiplier = 1
        for suffix, mult in [("billion", 1e9), ("B", 1e9), ("million", 1e6), ("M", 1e6)]:
            if s.lower().endswith(suffix.lower()):
                s = s[:len(s)-len(suffix)].strip()
                multiplier = mult
                break
        return float(s) * multiplier
    except (ValueError, TypeError):
        return None


def select_best_amount(amounts: list[dict]) -> str:
    """Pick the most likely project-level amount. Simple heuristic."""
    if not amounts:
        return ""
    if len(amounts) == 1:
        return amounts[0]["raw"]
    # Prefer amounts near "project", "total", "contract", "award"
    for a in amounts:
        ctx = a["context"].lower()
        if any(k in ctx for k in ("project", "total", "contract", "award", "appropriat", "budget")):
            return a["raw"]
    # Return the largest
    best = max(amounts, key=lambda a: a["value"])
    return best["raw"]


# ══════════════════════════════════════════════════════════════════════════════
#  FRICTION LEVEL
# ══════════════════════════════════════════════════════════════════════════════

_HIGH_FRICTION = [
    r"\blitigation\b", r"\blawsuit\b", r"\bappeal\b", r"\bdeferr?al\b", r"\bdeferred\b",
    r"\bfunding\s+gap\b", r"\bpermit\s+(?:issue|delay|denied)",
    r"\bstalled\b", r"\brebid\b", r"\bcancell?(?:ed|ation)\b", r"\bprotest\b",
    r"\blegal\s+challenge\b", r"\binjunction\b", r"\bmoratorium\b",
]

_MODERATE_FRICTION = [
    r"\bdepend(?:s|ency|ent)\b", r"\bcoordination\b", r"\binteragency\b",
    r"\bpending\s+(?:review|approval)", r"\bcontingent\b",
    r"\bunclear\s+(?:timeline|funding)", r"\bmultiple\s+(?:phases|departments)",
    r"\benvironmental\s+review\b", r"\bpermitting\b",
]


def infer_friction(text: str) -> str:
    text_lower = text.lower()
    for pat in _HIGH_FRICTION:
        if re.search(pat, text_lower):
            return "High"
    for pat in _MODERATE_FRICTION:
        if re.search(pat, text_lower):
            return "Moderate"
    return "Low"


# ══════════════════════════════════════════════════════════════════════════════
#  SIGNAL STRENGTH (scoring)
# ══════════════════════════════════════════════════════════════════════════════

def infer_signal_strength(
    text: str,
    procurement_stage: str,
    has_amount: bool,
    has_date: bool,
) -> str:
    score = 0

    # Specific identifiers
    if re.search(r"\b(?:ITB|RFQ|RFP|Resolution|Ordinance)\s*[-#]?\s*\d", text, re.I):
        score += 3
    if re.search(r"(?:project\s*(?:no|#|id))\s*:?\s*\w", text, re.I):
        score += 2

    # Concrete action
    if procurement_stage and procurement_stage != "Concept/Discussion":
        score += 2
    if procurement_stage == "Concept/Discussion":
        score += 1

    # Amount and date
    if has_amount:
        score += 2
    if has_date:
        score += 1

    # Approval/action verbs
    if re.search(r"(?:approv|authoriz|award|adopt|execute|ratif)", text, re.I):
        score += 1

    if score >= 6:
        return "High"
    elif score >= 3:
        return "Medium"
    else:
        return "Low"


# ══════════════════════════════════════════════════════════════════════════════
#  STRATEGIC FIT (basic default — Strong / Moderate / Monitor)
# ══════════════════════════════════════════════════════════════════════════════

def infer_strategic_fit(
    relevance_score: float,
    procurement_stage: str,
    signal_strength: str,
) -> str:
    if relevance_score >= 0.3 and signal_strength == "High":
        return "Strong Fit"
    if relevance_score >= 0.15 and signal_strength in ("High", "Medium"):
        return "Moderate Fit"
    if relevance_score >= 0.05:
        return "Monitor"
    return "No Fit"


# ══════════════════════════════════════════════════════════════════════════════
#  TRIGGER EVENT  (extract a short phrase)
# ══════════════════════════════════════════════════════════════════════════════

_TRIGGER_PATTERNS = [
    # Procurement triggers
    (r"((?:ITB|RFQ|RFP)\s*[-#]?\s*[\w-]+\s+.*?)(?:\.\s|$)", "procurement"),
    (r"(issuance\s+of\s+(?:ITB|RFQ|RFP)\s*[-#]?\s*[\w-]+.*?)(?:\.\s|$)", "procurement"),
    (r"((?:bid|solicitation)\s+(?:opening|submission|due|advertised).*?)(?:\.\s|$)", "procurement"),
    # Authorization triggers
    (r"((?:resolution|ordinance).*?authoriz\w+.*?(?:contract|design|construction|execute).*?)(?:\.\s|$)", "authorization"),
    (r"(authoriz\w+.*?(?:execute|award|negotiate).*?contract.*?)(?:\.\s|$)", "authorization"),
    (r"(commission\s+approv(?:al|ed)\s+(?:of\s+)?.*?)(?:\.\s|$)", "approval"),
    # Budget / funding triggers
    (r"((?:FY|fiscal\s+year)\s*\d{4}.*?(?:capital\s+budget|CIP|appropriat).*?)(?:\.\s|$)", "budget"),
    (r"((?:emergency\s+)?appropriation\s+of\s+\$[\d,.]+\s*(?:million|M|billion|B)?.*?)(?:\.\s|$)", "budget"),
    (r"((?:grant|funding)\s+(?:awarded?|allocated?|approved?)\s+(?:for\s+)?.*?)(?:\.\s|$)", "funding"),
    # Design / construction triggers
    (r"(design.{0,20}\d+%\s*complet\w+.*?)(?:\.\s|$)", "design"),
    (r"(construction\s+(?:to\s+)?(?:begin|start|commence)\w*\s+.*?)(?:\.\s|$)", "construction"),
    (r"(under\s+construction.*?(?:completion|complete).*?)(?:\.\s|$)", "construction"),
    # Committee / referral triggers
    (r"(refer(?:red)?\s+to\s+.*?committee.*?)(?:\.\s|$)", "referral"),
    # Change order / amendment
    (r"(change\s+order\s+\w+[-\d]+\s+approved.*?)(?:\.\s|$)", "amendment"),
    # Contract execution
    (r"((?:executed?|awarded?)\s+(?:a\s+)?contract\s+with\s+.*?)(?:\.\s|$)", "contract"),
]


def extract_trigger_event(text: str) -> str:
    text_oneline = re.sub(r"\s+", " ", text[:2000])
    for pat, _ in _TRIGGER_PATTERNS:
        m = re.search(pat, text_oneline, re.I)
        if m:
            return m.group(1).strip()[:200]
    return ""


# ══════════════════════════════════════════════════════════════════════════════
#  AGENCY & GEOGRAPHY
# ══════════════════════════════════════════════════════════════════════════════

_AGENCY_PATTERNS = [
    r"(?:(?:City|Village|Town|County|Municipality|Borough)\s+of\s+[\w\s]+?)(?=\s+(?:Commission|Department|Division|Office|Council|Board))",
    r"(?:[\w\s]+?\s+(?:County|District))(?=\s+(?:Water|Sewer|Stormwater|Public\s+Works))",
    r"(?:Department\s+of\s+[\w\s]+)",
]


def extract_agency(text: str, default: str) -> str:
    for pat in _AGENCY_PATTERNS:
        m = re.search(pat, text[:1000], re.I)
        if m:
            return m.group(0).strip()
    return default


# Generic sub-area patterns that work across any city
_GENERIC_GEO_PATS = [
    r"(?:city[-\s]?wide)",
    r"(?:village[-\s]?wide)",
    r"(?:district\s+\d+)",
    r"(?:zone\s+\d+)",
    r"(?:ward\s+\d+)",
]


def extract_geography(text: str, default: str, neighborhoods: list[str] | None = None) -> str:
    """Try to find a sub-area mention in the text.

    `neighborhoods` is an optional site-specific list of named areas
    (e.g. ["South Beach", "Indian Creek"]). When provided, those names
    are matched in addition to the generic patterns.
    """
    snippet = text[:2000]

    # Site-specific neighborhood names (highest priority)
    for name in (neighborhoods or []):
        if not name:
            continue
        # Match as a whole phrase, case-insensitive, with flexible whitespace
        pat = r"\b" + r"[\s\-]+".join(re.escape(p) for p in name.split()) + r"\b"
        m = re.search(pat, snippet, re.I)
        if m:
            found = m.group(0).strip().title()
            if found.lower() not in default.lower():
                return f"{default} ({found})"
            return default

    # Generic patterns (city-wide, district N, zone N, …)
    for pat in _GENERIC_GEO_PATS:
        m = re.search(pat, snippet, re.I)
        if m:
            found = m.group(0).strip().title()
            if found.lower() not in default.lower():
                return f"{default} ({found})"
            return default

    return default


# ══════════════════════════════════════════════════════════════════════════════
#  MEETING DATE
# ══════════════════════════════════════════════════════════════════════════════

def extract_meeting_date(text: str, fallback_date: str) -> str:
    """Try to find a meeting date from the text."""
    # Specific meeting date patterns
    pats = [
        r"(?:meeting|session|hearing)\s+(?:of|on|date)\s*:?\s*(\w+\s+\d{1,2},?\s+\d{4})",
        r"(\w+\s+\d{1,2},?\s+\d{4})\s+(?:meeting|session|regular|special|commission)",
        r"(?:dated?|held)\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})",
        r"(?:dated?|held)\s*:?\s*(\w+\s+\d{1,2},?\s+\d{4})",
    ]
    for pat in pats:
        m = re.search(pat, text[:2000], re.I)
        if m:
            return m.group(1).strip()
    return fallback_date
