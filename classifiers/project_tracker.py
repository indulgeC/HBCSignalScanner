"""
Project Tracker — builds per-project timelines and computes:
  - Momentum (Accelerating / Stable / Stalled / Unclear)
  - Friction (refined using cross-document evidence)
  - Merged "best" signal per project for the output grid
"""

from __future__ import annotations
import logging
import re
from typing import Dict, List, Optional

from models.signal import Signal
from classifiers.project_matcher import Project, group_signals_into_projects, _parse_value

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
#  STAGE ORDERING (lower index = more advanced)
# ══════════════════════════════════════════════════════════════════════════════

STAGE_ORDER: Dict[str, int] = {
    "Active Contract": 0,
    "Construction Authorization": 1,
    "Design Authorization": 2,
    "Prequalification (ITB/RFQ set up)": 3,
    "Funding Allocated": 4,
    "Committee Referral": 5,
    "Concept/Discussion": 6,
}

_STALL_KEYWORDS = re.compile(
    r"\b(?:postponed?|deferr?(?:ed|al)?|delay(?:ed)?|stalled|"
    r"no\s+action|tabled|held\s+over|"
    r"withdrawn?|cancell?(?:ed|ation)|suspended?|rebid|re-advertis)\b",
    re.I,
)


# ══════════════════════════════════════════════════════════════════════════════
#  MOMENTUM
# ══════════════════════════════════════════════════════════════════════════════

def compute_momentum(project: Project) -> str:
    """
    Determine project momentum from its signal history.

    Accelerating — moved to a more advanced stage across observations
    Stable      — remained at the same stage (multiple observations)
    Stalled     — deferred / postponed / no action / rebid indicators
    Unclear     — single observation, not enough data
    """
    signals = project.signals

    # Check for stall indicators in any signal
    for sig in signals:
        combined = f"{sig.trigger_event} {sig.evidence_snippet} {sig.strategic_notes}"
        if _STALL_KEYWORDS.search(combined):
            return "Stalled"

    stages = [s.procurement_stage for s in signals if s.procurement_stage]

    if len(stages) <= 1:
        return "Unclear"

    # Map to numeric order
    nums = [STAGE_ORDER.get(s, 99) for s in stages]
    unique_nums = list(dict.fromkeys(nums))  # preserve order, dedupe

    if len(unique_nums) == 1:
        return "Stable"

    # Check if progression goes toward more advanced (lower number)
    if unique_nums[-1] < unique_nums[0]:
        return "Accelerating"
    elif unique_nums[-1] > unique_nums[0]:
        # Went backward — unusual, might be a data issue
        return "Stable"
    else:
        return "Stable"


# ══════════════════════════════════════════════════════════════════════════════
#  REFINED FRICTION
# ══════════════════════════════════════════════════════════════════════════════

_HIGH_FRICTION_KW = re.compile(
    r"\b(?:litigation|lawsuit|appeal|deferral|deferred|"
    r"funding\s+gap|permit\s+(?:issue|delay|denied)|"
    r"stalled|rebid|cancell?(?:ed|ation)|protest|"
    r"legal\s+challenge|injunction|moratorium)\b",
    re.I,
)

_MOD_FRICTION_KW = re.compile(
    r"\b(?:depend(?:s|ency|ent)|coordination|interagency|"
    r"pending\s+(?:review|approval)|contingent|"
    r"unclear\s+(?:timeline|funding)|multiple\s+(?:phases|departments)|"
    r"environmental\s+review|permitting|unfunded|shortfall|"
    r"community\s+(?:opposition|concern)|hearing\s+required)\b",
    re.I,
)


def compute_friction(project: Project) -> str:
    """
    Refined friction using all signals in the project.
    Aggregates both pre-computed friction and cross-document evidence.
    """
    # First: respect friction already computed by rules.py on individual signals
    individual_frictions = [s.friction_level for s in project.signals if s.friction_level]
    if "High" in individual_frictions:
        return "High"

    # Second: scan combined evidence for additional friction signals
    for sig in project.signals:
        combined = f"{sig.evidence_snippet} {sig.trigger_event} {sig.strategic_notes}"
        if _HIGH_FRICTION_KW.search(combined):
            return "High"

    if "Moderate" in individual_frictions:
        return "Moderate"

    for sig in project.signals:
        combined = f"{sig.evidence_snippet} {sig.trigger_event} {sig.strategic_notes}"
        if _MOD_FRICTION_KW.search(combined):
            return "Moderate"

    return "Low"


# ══════════════════════════════════════════════════════════════════════════════
#  MERGE SIGNALS → ONE "BEST" SIGNAL PER PROJECT
# ══════════════════════════════════════════════════════════════════════════════

def merge_project_signals(project: Project) -> Signal:
    """
    Produce a single merged signal for a project by picking the best
    value for each field from all member signals.
    """
    signals = project.signals
    if len(signals) == 1:
        sig = signals[0]
        sig.momentum = compute_momentum(project)
        sig.friction_level = compute_friction(project)
        return sig

    # Pick the "primary" signal = highest confidence + most advanced stage
    def rank(s: Signal) -> tuple:
        stage_rank = STAGE_ORDER.get(s.procurement_stage, 99)
        strength_rank = {"High": 0, "Medium": 1, "Low": 2}.get(s.signal_strength, 3)
        return (stage_rank, strength_rank, -s.confidence_score)

    primary = min(signals, key=rank)

    # Build merged signal starting from primary
    merged = Signal(
        signal_title=_best_title(signals),
        agency=primary.agency,
        geography=_best_geography(signals),
        sector=primary.sector,
        estimated_value=_best_value(signals),
        expected_timeline=primary.expected_timeline,
        meeting_date=_latest_date(signals),
        signal_type=primary.signal_type,
        procurement_stage=project.latest_stage,
        lifecycle_stage=primary.lifecycle_stage,
        signal_strength=_best_strength(signals),
        strategic_fit=primary.strategic_fit,
        friction_level=compute_friction(project),
        momentum=compute_momentum(project),
        trigger_event=_best_trigger(signals),
        strategic_notes=_merged_notes(project),
        source_link=primary.source_link,
        source_file_url=primary.source_file_url,
        source_page_url=primary.source_page_url,
        evidence_snippet=primary.evidence_snippet,
        evidence_page=primary.evidence_page,
        confidence_score=max(s.confidence_score for s in signals),
        extraction_method="merged",
        raw_amounts=primary.raw_amounts,
        doc_url=primary.doc_url,
        doc_type=primary.doc_type,
        relevance_score=max(s.relevance_score for s in signals),
    )
    return merged


# ── Field merge helpers ───────────────────────────────────────────────────────

def _best_title(signals: List[Signal]) -> str:
    """Pick the shortest non-generic title."""
    titled = [s for s in signals if s.signal_title and len(s.signal_title) > 10]
    if not titled:
        return signals[0].signal_title
    # Prefer titles without pipe separators (table rows) and with fewest chars
    scored = []
    for s in titled:
        penalty = 100 if "|" in s.signal_title else 0
        scored.append((len(s.signal_title) + penalty, s.signal_title))
    scored.sort()
    return scored[0][1]


def _best_geography(signals: List[Signal]) -> str:
    """Pick the most specific geography."""
    geos = [s.geography for s in signals if s.geography]
    if not geos:
        return ""
    # Prefer ones with parenthetical detail
    detailed = [g for g in geos if "(" in g]
    if detailed:
        return detailed[0]
    return geos[0]


def _best_value(signals: List[Signal]) -> str:
    """Pick the largest dollar value."""
    best = ""
    best_num = 0.0
    for s in signals:
        v = _parse_value(s.estimated_value)
        if v and v > best_num:
            best_num = v
            best = s.estimated_value
    return best


def _best_strength(signals: List[Signal]) -> str:
    """Return the highest strength."""
    order = {"High": 0, "Medium": 1, "Low": 2}
    best = min(signals, key=lambda s: order.get(s.signal_strength, 3))
    return best.signal_strength


def _best_trigger(signals: List[Signal]) -> str:
    """Pick the longest non-empty trigger."""
    triggers = [s.trigger_event for s in signals if s.trigger_event]
    if not triggers:
        return ""
    return max(triggers, key=len)


def _latest_date(signals: List[Signal]) -> str:
    """Return the most recent date."""
    dates = [s.meeting_date for s in signals if s.meeting_date]
    if not dates:
        return ""
    return max(dates)


def _merged_notes(project: Project) -> str:
    """Generate notes that reflect the merged project view."""
    stages = project.stages_seen
    n = len(project.signals)
    momentum = compute_momentum(project)

    parts = []
    if n > 1:
        parts.append(f"Project appears in {n} sources.")
    if len(stages) > 1:
        parts.append(f"Progressed through: {' → '.join(stages)}.")
    elif stages:
        parts.append(f"Current stage: {stages[0]}.")
    if momentum != "Unclear":
        parts.append(f"Momentum: {momentum}.")

    return " ".join(parts)[:500]


# ══════════════════════════════════════════════════════════════════════════════
#  TOP-LEVEL API
# ══════════════════════════════════════════════════════════════════════════════

def track_and_merge(
    signals: List[Signal],
    merge_threshold: float = 0.45,
    keep_all: bool = False,
) -> tuple[List[Signal], List[Project]]:
    """
    Full Phase 3 pipeline:
      1. Group signals into projects
      2. Compute momentum & friction per project
      3. Merge each project into a single output signal
      4. Return (merged_signals, projects)

    If keep_all=True, also return un-merged individual signals
    with updated momentum/friction fields.
    """
    projects = group_signals_into_projects(signals, threshold=merge_threshold)

    logger.info(
        "Project matching: %d signals → %d projects (threshold=%.2f)",
        len(signals), len(projects), merge_threshold,
    )

    merged_signals: List[Signal] = []
    for proj in projects:
        if len(proj.signals) > 1:
            logger.info(
                "  Project %s (%d signals): %s",
                proj.project_id, len(proj.signals),
                [s.signal_title[:50] for s in proj.signals],
            )

        if keep_all:
            # Update momentum/friction on each individual signal
            mom = compute_momentum(proj)
            fric = compute_friction(proj)
            for sig in proj.signals:
                sig.momentum = mom
                sig.friction_level = fric
                merged_signals.append(sig)
        else:
            merged = merge_project_signals(proj)
            merged_signals.append(merged)

    return merged_signals, projects
