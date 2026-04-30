"""
HTML parser — extracts title, date, body text, and structured sections
from crawled HTML pages.
"""

from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)


@dataclass
class ParsedChunk:
    """One logical unit extracted from a document."""
    text: str
    title: str = ""
    date: str = ""
    source_url: str = ""
    file_url: str = ""
    page_number: str = ""
    chunk_type: str = ""       # section / agenda_item / solicitation / budget_line
    category: str = ""         # inherited from CrawlResult
    raw_html: str = ""
    doc_type: str = "html"


def parse_html(html: str, url: str, category: str = "") -> List[ParsedChunk]:
    """Parse an HTML page into one or more chunks."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove boilerplate
    for tag in soup.find_all(["nav", "header", "footer", "script", "style", "noscript"]):
        tag.decompose()

    title = ""
    tag = soup.find("title")
    if tag:
        title = tag.get_text(strip=True)

    # Try to find a page-level date
    page_date = _extract_date_from_soup(soup)

    # Attempt structured splitting
    chunks = _try_agenda_split(soup, url, category, title, page_date)
    if chunks:
        return chunks

    chunks = _try_solicitation_split(soup, url, category, title, page_date)
    if chunks:
        return chunks

    chunks = _try_project_split(soup, url, category, title, page_date)
    if chunks:
        return chunks

    # Fallback: treat entire main content as one chunk
    main = soup.find("main") or soup.find("article") or soup.find("div", class_=re.compile(r"content|entry|post"))
    body_text = (main or soup).get_text(separator="\n", strip=True)

    if len(body_text) < 50:
        logger.debug("parse_html: skipping %s — body too short (%d chars)", url, len(body_text))
        return []

    return [ParsedChunk(
        text=body_text[:15000],
        title=title,
        date=page_date,
        source_url=url,
        chunk_type="section",
        category=category,
    )]


# ── Agenda splitting ─────────────────────────────────────────────────────────

def _try_agenda_split(
    soup: BeautifulSoup, url: str, category: str, title: str, date: str
) -> List[ParsedChunk]:
    """Split a meeting/agenda page into per-item chunks."""
    if category != "meetings" and not re.search(r"agenda|meeting|minute", title, re.I):
        return []

    items: List[ParsedChunk] = []

    # Pattern 1: numbered list items or headings like "Item 5.", "C4F", "R5A"
    headings = soup.find_all(["h2", "h3", "h4", "strong", "b"])
    agenda_heads = []
    for h in headings:
        txt = h.get_text(strip=True)
        if re.match(r"^(item\s+\d|[A-Z]\d|[RCDA]\d|consent|public hearing|discussion|ordinance|resolution)", txt, re.I):
            agenda_heads.append(h)

    if agenda_heads:
        for i, head in enumerate(agenda_heads):
            # Gather text until next heading
            parts = [head.get_text(strip=True)]
            sib = head.find_next_sibling()
            while sib and sib not in agenda_heads[i+1:i+2]:
                parts.append(sib.get_text(strip=True) if isinstance(sib, Tag) else str(sib).strip())
                sib = sib.find_next_sibling()
                if sib is None:
                    break
            text = "\n".join(p for p in parts if p)
            if len(text) > 20:
                items.append(ParsedChunk(
                    text=text[:8000],
                    title=parts[0][:200],
                    date=date,
                    source_url=url,
                    chunk_type="agenda_item",
                    category=category,
                ))

    # Pattern 2: table rows (common in Novus Agenda, etc.)
    if not items:
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                row_text = " | ".join(c.get_text(strip=True) for c in cells)
                if len(row_text) > 30:
                    items.append(ParsedChunk(
                        text=row_text[:4000],
                        title=row_text[:200],
                        date=date,
                        source_url=url,
                        chunk_type="agenda_item",
                        category=category,
                    ))

    return items


# ── Solicitation splitting ────────────────────────────────────────────────────

def _try_solicitation_split(
    soup: BeautifulSoup, url: str, category: str, title: str, date: str
) -> List[ParsedChunk]:
    """Split a procurement/bid page into per-solicitation chunks."""
    if category != "procurement" and not re.search(r"bid|solicitation|procurement|rfp|rfq|itb", title, re.I):
        return []

    items: List[ParsedChunk] = []

    # Look for bid tables
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if any(k in " ".join(headers) for k in ("solicitation", "bid", "title", "description", "rfp", "itb")):
            for row in table.find_all("tr")[1:]:
                cells = row.find_all("td")
                row_text = " | ".join(c.get_text(strip=True) for c in cells)
                if len(row_text) > 20:
                    items.append(ParsedChunk(
                        text=row_text[:4000],
                        title=row_text[:200],
                        date=date,
                        source_url=url,
                        chunk_type="solicitation",
                        category="procurement",
                    ))

    # Look for divs/cards with bid info
    if not items:
        for card in soup.find_all("div", class_=re.compile(r"bid|solicitation|opportunity|listing", re.I)):
            text = card.get_text(separator="\n", strip=True)
            if len(text) > 30:
                items.append(ParsedChunk(
                    text=text[:4000],
                    title=text.split("\n")[0][:200],
                    date=date,
                    source_url=url,
                    chunk_type="solicitation",
                    category="procurement",
                ))

    return items


# ── CIP / Project-based splitting ─────────────────────────────────────────────

def _try_project_split(
    soup: BeautifulSoup, url: str, category: str, title: str, date: str
) -> List[ParsedChunk]:
    """Split CIP / capital-project pages into per-project chunks."""
    if category not in ("cip", "budget") and not re.search(
        r"capital|CIP|improvement|project", title, re.I
    ):
        return []

    items: List[ParsedChunk] = []

    # Pattern 1: <strong>Project: ...</strong> or <b>Project: ...</b> headings
    project_heads = []
    for tag in soup.find_all(["strong", "b", "h2", "h3", "h4"]):
        txt = tag.get_text(strip=True)
        if re.match(r"^(?:Project\s*[:：]|[A-Z]{1,4}[-\s]?\d{4}[-\s]?\d{1,4}\s*[:：\-]|[A-Z][\w\s]{3,40}(?:Improvement|Program|Replacement|Rehabilitation|Upgrade|Construction|Repair))", txt, re.I):
            project_heads.append(tag)

    if len(project_heads) >= 2:
        for i, head in enumerate(project_heads):
            # Get the containing block (often a <p> wrapping the <strong>)
            container = head
            if head.parent and head.parent.name in ("p", "div", "li"):
                container = head.parent

            # Use separator to avoid concatenation issues with <br> tags
            parts = [container.get_text(separator="\n", strip=True)]

            # Walk next siblings of the container
            sib = container.find_next_sibling()
            limit = 15
            while sib and limit > 0:
                # Check if this sibling contains the next project head
                if isinstance(sib, Tag):
                    for next_head in project_heads[i+1:i+2]:
                        if next_head == sib or sib.find(next_head.name, string=next_head.string):
                            sib = None
                            break
                        if next_head in sib.descendants:
                            sib = None
                            break
                if sib is None:
                    break
                sib_text = sib.get_text(separator="\n", strip=True) if isinstance(sib, Tag) else ""
                if sib_text:
                    parts.append(sib_text)
                sib = sib.find_next_sibling()
                limit -= 1

            text = "\n".join(p for p in parts if p)
            # Use the heading tag text for title, not the full container
            heading_text = head.get_text(strip=True).replace("Project:", "").replace("Project：", "").strip()

            text = "\n".join(p for p in parts if p)
            if len(text) > 40:
                items.append(ParsedChunk(
                    text=text[:8000],
                    title=heading_text[:200],
                    date=date,
                    source_url=url,
                    chunk_type="budget_line",
                    category=category or "cip",
                ))

    return items


# ── Date extraction ───────────────────────────────────────────────────────────

DATE_PATTERNS = [
    r"\b(\d{1,2}/\d{1,2}/\d{4})\b",
    r"\b(\d{4}-\d{2}-\d{2})\b",
    r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})\b",
    r"\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b",
]

def _extract_date_from_soup(soup: BeautifulSoup) -> str:
    # meta tags
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or meta.get("property") or "").lower()
        if "date" in name:
            content = meta.get("content", "")
            if content:
                return content[:30]

    # Text scan (first match near the top)
    top_text = soup.get_text(separator=" ", strip=True)[:2000]
    for pat in DATE_PATTERNS:
        m = re.search(pat, top_text, re.I)
        if m:
            return m.group(1)
    return ""
