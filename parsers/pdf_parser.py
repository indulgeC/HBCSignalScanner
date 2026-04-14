"""
PDF parser — extracts text from PDF files using pymupdf,
splits into logical chunks (sections, agenda items, budget lines).
"""

from __future__ import annotations
import os
import re
from typing import List

import fitz  # pymupdf

from parsers.html_parser import ParsedChunk, DATE_PATTERNS

# ── Public API ────────────────────────────────────────────────────────────────

def parse_pdf(file_path: str, source_url: str, category: str = "") -> List[ParsedChunk]:
    """Extract text from a PDF and split into signal-level chunks."""
    if not os.path.exists(file_path):
        return []

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        return [ParsedChunk(
            text=f"[PDF parse error: {e}]",
            source_url=source_url,
            doc_type="pdf",
            category=category,
        )]

    pages_text: list[tuple[int, str]] = []
    for i, page in enumerate(doc):
        text = page.get_text("text")
        if text.strip():
            pages_text.append((i + 1, text))
    doc.close()

    if not pages_text:
        return []

    full_text = "\n\n".join(t for _, t in pages_text)

    # Try structured splitting
    chunks = _try_agenda_pdf_split(pages_text, source_url, category)
    if chunks:
        return chunks

    chunks = _try_budget_pdf_split(pages_text, source_url, category)
    if chunks:
        return chunks

    # Fallback: split by pages or big sections
    return _fallback_split(pages_text, source_url, category)


# ── Agenda PDF splitting ─────────────────────────────────────────────────────

AGENDA_ITEM_RE = re.compile(
    r"^(?:Item\s+\d|[A-Z]\d[A-Z]?|Agenda\s+Item|CONSENT\s+AGENDA|PUBLIC\s+HEARING|"
    r"DISCUSSION\s+ITEM|ORDINANCE|RESOLUTION|R\d[A-Z]|C\d[A-Z])",
    re.IGNORECASE | re.MULTILINE,
)

def _try_agenda_pdf_split(
    pages_text: list[tuple[int, str]], source_url: str, category: str
) -> List[ParsedChunk]:
    """Split an agenda/minutes PDF into per-item chunks."""
    full = "\n\n".join(t for _, t in pages_text)
    if not AGENDA_ITEM_RE.search(full):
        return []

    # Find all item boundaries
    splits = list(AGENDA_ITEM_RE.finditer(full))
    if len(splits) < 2:
        return []

    chunks: list[ParsedChunk] = []
    for i, match in enumerate(splits):
        start = match.start()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(full)
        block = full[start:end].strip()
        if len(block) < 30:
            continue

        # Approximate page number
        page_num = _approx_page(pages_text, start)
        item_title = block.split("\n")[0][:200]
        date = _first_date(block)

        chunks.append(ParsedChunk(
            text=block[:8000],
            title=item_title,
            date=date,
            source_url=source_url,
            page_number=str(page_num),
            chunk_type="agenda_item",
            category=category or "meetings",
            doc_type="pdf",
        ))

    return chunks


# ── Budget/CIP PDF splitting ─────────────────────────────────────────────────

BUDGET_LINE_RE = re.compile(
    r"(?:project\s*(?:no|#|id|name)|fund\s+\d|account|appropriation|"
    r"\$[\d,]+(?:\.\d{2})?|\bCIP\b|\bcapital\b)",
    re.IGNORECASE,
)

def _try_budget_pdf_split(
    pages_text: list[tuple[int, str]], source_url: str, category: str
) -> List[ParsedChunk]:
    """Split a budget/CIP PDF into per-project chunks."""
    full = "\n\n".join(t for _, t in pages_text)
    if not re.search(r"capital|budget|CIP|appropriat", full, re.I):
        return []

    # Try to find project blocks separated by headers or blank lines
    # Look for lines that start with project names/numbers
    project_header_re = re.compile(
        r"^(?:Project\s+(?:Name|No|#|ID)|[A-Z][A-Z\s]{5,40}(?:IMPROVEMENT|PROGRAM|PROJECT|REPLACEMENT|REHABILITATION))",
        re.IGNORECASE | re.MULTILINE,
    )
    splits = list(project_header_re.finditer(full))

    if len(splits) >= 2:
        chunks = []
        for i, match in enumerate(splits):
            start = match.start()
            end = splits[i + 1].start() if i + 1 < len(splits) else min(start + 5000, len(full))
            block = full[start:end].strip()
            if len(block) < 40:
                continue
            page_num = _approx_page(pages_text, start)
            chunks.append(ParsedChunk(
                text=block[:8000],
                title=block.split("\n")[0][:200],
                date=_first_date(block),
                source_url=source_url,
                page_number=str(page_num),
                chunk_type="budget_line",
                category=category or "budget",
                doc_type="pdf",
            ))
        if chunks:
            return chunks

    # No structured split found for budget
    return []


# ── Fallback: page-level split ────────────────────────────────────────────────

def _fallback_split(
    pages_text: list[tuple[int, str]], source_url: str, category: str
) -> List[ParsedChunk]:
    """Concatenate pages into ~4000-char chunks."""
    chunks: list[ParsedChunk] = []
    buf = ""
    start_page = 1

    for page_num, text in pages_text:
        if len(buf) + len(text) > 4000 and buf:
            chunks.append(ParsedChunk(
                text=buf,
                title=buf.split("\n")[0][:200],
                date=_first_date(buf),
                source_url=source_url,
                page_number=f"{start_page}-{page_num - 1}",
                chunk_type="section",
                category=category,
                doc_type="pdf",
            ))
            buf = text
            start_page = page_num
        else:
            buf += "\n\n" + text

    if buf.strip():
        last_page = pages_text[-1][0] if pages_text else start_page
        chunks.append(ParsedChunk(
            text=buf,
            title=buf.split("\n")[0][:200],
            date=_first_date(buf),
            source_url=source_url,
            page_number=f"{start_page}-{last_page}",
            chunk_type="section",
            category=category,
            doc_type="pdf",
        ))

    return chunks


# ── Helpers ───────────────────────────────────────────────────────────────────

def _approx_page(pages_text: list[tuple[int, str]], char_offset: int) -> int:
    """Estimate which page a character offset falls on."""
    running = 0
    for page_num, text in pages_text:
        running += len(text) + 2  # +2 for \n\n joiner
        if running >= char_offset:
            return page_num
    return pages_text[-1][0] if pages_text else 1


def _first_date(text: str) -> str:
    for pat in DATE_PATTERNS:
        m = re.search(pat, text[:1000], re.I)
        if m:
            return m.group(1)
    return ""
