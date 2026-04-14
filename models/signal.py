"""Signal data model — one row in the output grid."""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
import json


@dataclass
class Signal:
    # ── Core identifiers ──────────────────────────────────────────────
    signal_title: str = ""
    agency: str = ""
    geography: str = ""
    sector: str = ""

    # ── Value & timing ────────────────────────────────────────────────
    estimated_value: str = ""
    expected_timeline: str = ""
    meeting_date: str = ""

    # ── Classification ────────────────────────────────────────────────
    signal_type: str = ""
    procurement_stage: str = ""
    lifecycle_stage: str = ""

    # ── Qualitative assessments ───────────────────────────────────────
    signal_strength: str = ""      # High / Medium / Low
    strategic_fit: str = ""        # Strong Fit / Moderate Fit / Monitor / No Fit
    friction_level: str = ""       # Low / Moderate / High
    momentum: str = ""             # Accelerating / Stable / Stalled / Unclear

    # ── Descriptive ───────────────────────────────────────────────────
    trigger_event: str = ""
    strategic_notes: str = ""

    # ── Source provenance ─────────────────────────────────────────────
    source_link: str = ""
    source_file_url: str = ""
    source_page_url: str = ""

    # ── Hidden / audit fields ─────────────────────────────────────────
    evidence_snippet: str = ""
    evidence_page: str = ""
    confidence_score: float = 0.0
    extraction_method: str = ""    # rule / ai / derived
    raw_amounts: list = field(default_factory=list)

    # ── Internal bookkeeping ──────────────────────────────────────────
    doc_url: str = ""
    doc_type: str = ""             # html / pdf / docx
    chunk_index: int = 0
    relevance_score: float = 0.0

    # ── Export helpers ────────────────────────────────────────────────
    DISPLAY_COLUMNS = [
        "signal_title", "agency", "geography", "sector",
        "estimated_value", "expected_timeline", "meeting_date",
        "signal_type", "procurement_stage", "lifecycle_stage",
        "signal_strength", "strategic_fit", "friction_level", "momentum",
        "trigger_event", "strategic_notes",
        "source_link",
    ]

    AUDIT_COLUMNS = [
        "evidence_snippet", "evidence_page", "confidence_score",
        "extraction_method", "source_file_url", "source_page_url",
    ]

    def to_display_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.DISPLAY_COLUMNS}

    def to_full_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_full_dict(), ensure_ascii=False, default=str)
