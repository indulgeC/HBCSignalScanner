"""
Project Matcher — groups signals referring to the same real-world project.

Matching strategy (scored):
  1. Exact project ID match (CIP-SW-2025-012, ITB 2025-089-WF, etc.)
  2. Fuzzy name similarity (Jaccard on tokenised title words)
  3. Dollar-amount proximity (within 20%)
  4. Geography overlap
  5. Sector match

Two signals merge into the same project when their combined
similarity score exceeds a configurable threshold.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from models.signal import Signal


# ══════════════════════════════════════════════════════════════════════════════
#  PROJECT ID EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

_PROJECT_ID_PATTERNS = [
    re.compile(r"\b(CIP[-\s]?\w{1,4}[-\s]?\d{4}[-\s]?\d{1,4})\b", re.I),
    re.compile(r"\b(ITB\s*[-#]?\s*\d{4}[-\s]?\d{2,4}[-\s]?\w{0,4})\b", re.I),
    re.compile(r"\b(RFQ\s*[-#]?\s*\d{4}[-\s]?\d{2,4}[-\s]?\w{0,4})\b", re.I),
    re.compile(r"\b(RFP\s*[-#]?\s*\d{4}[-\s]?\d{2,4}[-\s]?\w{0,4})\b", re.I),
    re.compile(r"\b(FBO[-\s]?\d{4}[-\s]?\w{1,4}[-\s]?\d{1,4})\b", re.I),
    re.compile(r"(?:project\s*(?:id|no|#|number)\s*[:.]?\s*)([\w-]+)", re.I),
    re.compile(r"\b(Resolution\s+(?:No\.?\s*)?\d{4}[-\s]?\d+)\b", re.I),
]


def extract_project_ids(signal: Signal) -> Set[str]:
    """Extract all identifiable project/solicitation IDs from a signal."""
    ids: Set[str] = set()
    text = f"{signal.signal_title} {signal.trigger_event} {signal.evidence_snippet}"
    for pat in _PROJECT_ID_PATTERNS:
        for m in pat.finditer(text):
            # Normalise: strip whitespace, uppercase
            raw = re.sub(r"\s+", "-", m.group(1).strip()).upper()
            ids.add(raw)
    return ids


# ══════════════════════════════════════════════════════════════════════════════
#  TEXT SIMILARITY
# ══════════════════════════════════════════════════════════════════════════════

_STOPWORDS = {
    "a", "an", "the", "of", "for", "and", "in", "to", "on", "at", "by",
    "with", "from", "city", "miami", "beach", "county", "project",
    "services", "improvement", "improvements", "program",
}


def _tokenize(text: str) -> Set[str]:
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    return words - _STOPWORDS


def jaccard_similarity(a: str, b: str) -> float:
    """Jaccard similarity on tokenized words (minus stopwords)."""
    ta = _tokenize(a)
    tb = _tokenize(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  AMOUNT PROXIMITY
# ══════════════════════════════════════════════════════════════════════════════

def _parse_value(v: str) -> Optional[float]:
    """Parse a dollar string to float."""
    if not v:
        return None
    s = v.replace("$", "").replace(",", "").strip()
    multiplier = 1.0
    for suffix, mult in [("billion", 1e9), ("B", 1e9), ("million", 1e6), ("M", 1e6)]:
        if s.lower().endswith(suffix.lower()):
            s = s[:len(s) - len(suffix)].strip()
            multiplier = mult
            break
    try:
        return float(s) * multiplier
    except (ValueError, TypeError):
        return None


def amount_similarity(a: str, b: str) -> float:
    """Return 1.0 if amounts are within 20%, 0.5 if within 50%, else 0."""
    va = _parse_value(a)
    vb = _parse_value(b)
    if va is None or vb is None:
        return 0.0  # can't compare
    if va == 0 and vb == 0:
        return 1.0
    ratio = min(va, vb) / max(va, vb) if max(va, vb) > 0 else 0
    if ratio >= 0.8:
        return 1.0
    if ratio >= 0.5:
        return 0.5
    return 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  COMPOSITE SIMILARITY SCORE
# ══════════════════════════════════════════════════════════════════════════════

def signal_similarity(a: Signal, b: Signal) -> float:
    """
    Compute how likely two signals refer to the same project.
    Returns 0.0 – 1.0.
    """
    # 1. Exact ID match → instant high score
    ids_a = extract_project_ids(a)
    ids_b = extract_project_ids(b)
    if ids_a and ids_b and (ids_a & ids_b):
        return 1.0

    score = 0.0
    weights_total = 0.0

    # 2. Title similarity (weight 0.40)
    title_sim = jaccard_similarity(a.signal_title, b.signal_title)
    score += 0.40 * title_sim
    weights_total += 0.40

    # 3. Evidence / trigger similarity (weight 0.20)
    ev_text_a = f"{a.trigger_event} {a.evidence_snippet}"
    ev_text_b = f"{b.trigger_event} {b.evidence_snippet}"
    ev_sim = jaccard_similarity(ev_text_a, ev_text_b)
    score += 0.20 * ev_sim
    weights_total += 0.20

    # 4. Amount proximity (weight 0.15)
    amt_sim = amount_similarity(a.estimated_value, b.estimated_value)
    score += 0.15 * amt_sim
    weights_total += 0.15

    # 5. Geography match (weight 0.10)
    geo_sim = 1.0 if a.geography and b.geography and a.geography.lower() == b.geography.lower() else 0.3
    score += 0.10 * geo_sim
    weights_total += 0.10

    # 6. Sector match (weight 0.15)
    sector_sim = 1.0 if a.sector == b.sector else 0.0
    score += 0.15 * sector_sim
    weights_total += 0.15

    return round(score / weights_total if weights_total else 0, 3)


# ══════════════════════════════════════════════════════════════════════════════
#  PROJECT GROUPING
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Project:
    """A group of signals all referring to the same real-world project."""
    project_id: str
    project_name: str
    signals: List[Signal] = field(default_factory=list)
    all_ids: Set[str] = field(default_factory=set)

    @property
    def latest_stage(self) -> str:
        """Return the most advanced procurement stage seen."""
        stage_order = [
            "Active Contract",
            "Construction Authorization",
            "Design Authorization",
            "Prequalification (ITB/RFQ set up)",
            "Funding Allocated",
            "Committee Referral",
            "Concept/Discussion",
        ]
        for stage in stage_order:
            if any(s.procurement_stage == stage for s in self.signals):
                return stage
        return ""

    @property
    def stages_seen(self) -> List[str]:
        """All unique stages in chronological order of first appearance."""
        seen: list[str] = []
        for s in self.signals:
            if s.procurement_stage and s.procurement_stage not in seen:
                seen.append(s.procurement_stage)
        return seen

    @property
    def best_value(self) -> str:
        """Return the largest dollar value across all signals."""
        best = ""
        best_num = 0.0
        for s in self.signals:
            v = _parse_value(s.estimated_value)
            if v and v > best_num:
                best_num = v
                best = s.estimated_value
        return best

    @property
    def date_range(self) -> Tuple[str, str]:
        """Earliest and latest meeting dates."""
        dates = [s.meeting_date for s in self.signals if s.meeting_date]
        if not dates:
            return ("", "")
        return (min(dates), max(dates))


def group_signals_into_projects(
    signals: List[Signal],
    threshold: float = 0.45,
) -> List[Project]:
    """
    Cluster signals into projects using pairwise similarity.
    Simple greedy single-linkage: assign each signal to the first
    project whose any member exceeds the similarity threshold.
    """
    projects: List[Project] = []

    for sig in signals:
        sig_ids = extract_project_ids(sig)
        best_project: Optional[Project] = None
        best_score = 0.0

        for proj in projects:
            # Check ID overlap first (fast path)
            if sig_ids and proj.all_ids and (sig_ids & proj.all_ids):
                best_project = proj
                best_score = 1.0
                break

            # Pairwise similarity against project members
            for member in proj.signals:
                sim = signal_similarity(sig, member)
                if sim > best_score:
                    best_score = sim
                    best_project = proj

        if best_project and best_score >= threshold:
            best_project.signals.append(sig)
            best_project.all_ids |= sig_ids
        else:
            # Start new project
            pid = f"P-{len(projects)+1:04d}"
            pname = sig.signal_title[:100]
            proj = Project(
                project_id=pid,
                project_name=pname,
                signals=[sig],
                all_ids=sig_ids,
            )
            projects.append(proj)

    return projects
