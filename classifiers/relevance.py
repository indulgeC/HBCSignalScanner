"""
Relevance scorer — scores each chunk against selected sectors
using keyword matching with positional weighting.
"""

from __future__ import annotations
import re
from typing import Dict, List, Set


def load_sector_keywords(sectors_config: dict) -> Dict[str, List[str]]:
    """Load sector → keyword list from config."""
    result = {}
    for sector_name, info in sectors_config.get("sectors", {}).items():
        result[sector_name] = info.get("keywords", [])
    return result


def score_relevance(
    text: str,
    sector: str,
    keyword_map: Dict[str, List[str]],
) -> float:
    """
    Score how relevant a text chunk is to a given sector.
    Returns 0.0 – 1.0.
    """
    keywords = keyword_map.get(sector, [])
    if not keywords:
        return 0.0

    text_lower = text.lower()
    total_words = max(len(text_lower.split()), 1)

    hits = 0
    unique_kw_hits = 0
    title_bonus = 0

    # Check first 200 chars (title zone) for bonus
    title_zone = text_lower[:200]

    for kw in keywords:
        kw_lower = kw.lower()
        pattern = re.compile(r"\b" + re.escape(kw_lower) + r"\b", re.I)
        matches = pattern.findall(text_lower)
        count = len(matches)
        if count > 0:
            unique_kw_hits += 1
            hits += count
            if pattern.search(title_zone):
                title_bonus += 0.15

    if hits == 0:
        return 0.0

    # Score components
    frequency_score = min(hits / (total_words / 50), 1.0)  # density
    coverage_score = min(unique_kw_hits / max(len(keywords) * 0.3, 1), 1.0)  # breadth
    title_score = min(title_bonus, 0.3)

    raw = 0.4 * frequency_score + 0.35 * coverage_score + 0.25 * title_score
    return round(min(raw, 1.0), 3)


def is_relevant(
    text: str,
    selected_sectors: List[str],
    keyword_map: Dict[str, List[str]],
    threshold: float = 0.05,
) -> tuple[bool, str, float]:
    """
    Check if text is relevant to any of the selected sectors.
    Returns (is_relevant, best_sector, best_score).
    """
    best_sector = ""
    best_score = 0.0

    for sector in selected_sectors:
        score = score_relevance(text, sector, keyword_map)
        if score > best_score:
            best_score = score
            best_sector = sector

    return best_score >= threshold, best_sector, best_score
