"""
PrimeGov API Crawler — fetches meeting agendas from PrimeGov public portal API.

Miami Beach (and many other cities) use PrimeGov for meeting management.
This crawler uses the REST API to:
  1. List meetings by year (archived) or upcoming
  2. Fetch HTML agenda content for each meeting
  3. Return CrawlResult objects compatible with the existing pipeline
"""

from __future__ import annotations
import logging
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import List, Optional
from urllib.parse import urljoin

import requests

from crawler.discover import CrawlResult

logger = logging.getLogger(__name__)


class _TextExtractor(HTMLParser):
    """Simple HTML-to-text converter that strips scripts/styles."""

    def __init__(self):
        super().__init__()
        self.text_parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            t = data.strip()
            if t:
                self.text_parts.append(t)


class PrimeGovCrawler:
    """Fetch meeting data from a PrimeGov public portal API."""

    # Default PrimeGov API base for Miami Beach
    DEFAULT_API_BASE = "https://miamibeachfl.primegov.com"

    def __init__(
        self,
        site_config: dict,
        year: Optional[int] = None,
        years: Optional[List[int]] = None,
        max_pages: Optional[int] = None,
    ):
        self.cfg = site_config
        # Normalize year/years into a single list (or None = upcoming only)
        if years:
            self.years = list(years)
        elif year:
            self.years = [year]
        else:
            self.years = None
        self.max_pages = max_pages or site_config.get("max_pages", 200)
        self.api_base = site_config.get(
            "primegov_api_base", self.DEFAULT_API_BASE
        )
        self.delay = site_config.get("request_delay_seconds", 1.0)

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/html, */*",
        })

        self.results: List[CrawlResult] = []

    # ── Public API ───────────────────────────────────────────────────

    def crawl(self) -> List[CrawlResult]:
        """Fetch meetings and their HTML agendas."""
        meetings = self._list_meetings()
        logger.info(
            "PrimeGov: found %d meetings (years=%s)", len(meetings), self.years
        )

        count = 0
        for meeting in meetings:
            if count >= self.max_pages:
                break

            meeting_id = meeting.get("id")
            title = meeting.get("title", "")
            date_str = meeting.get("date", "")
            date_time = meeting.get("dateTime", "")

            # Find the HTML agenda document
            html_doc_id = None
            pdf_doc_id = None
            for doc in meeting.get("documentList", []):
                if doc.get("compileOutputType") == 3:  # HTML Agenda
                    html_doc_id = doc["id"]
                elif doc.get("compileOutputType") == 1:  # PDF Agenda
                    if "agenda" in doc.get("templateName", "").lower():
                        pdf_doc_id = doc["id"]

            if not html_doc_id:
                logger.debug("Skipping meeting %s (%s) — no HTML agenda", meeting_id, title)
                continue

            # Fetch the HTML agenda
            agenda_url = (
                f"{self.api_base}/Portal/Meeting"
                f"?compiledMeetingDocumentFileId={html_doc_id}"
            )

            try:
                resp = self.session.get(agenda_url, timeout=30)
                if resp.status_code != 200:
                    logger.warning("HTTP %d for agenda %s", resp.status_code, agenda_url)
                    continue

                html_content = resp.text

                result = CrawlResult(
                    url=agenda_url,
                    final_url=agenda_url,
                    category="meetings",
                    content_type="text/html",
                    html=html_content,
                    title=f"{title} — {date_str}",
                    depth=0,
                    status_code=200,
                )
                self.results.append(result)
                count += 1

                logger.info(
                    "[%d/%d] %s — %s (doc_id=%s)",
                    count, self.max_pages, date_str, title, html_doc_id,
                )

            except requests.RequestException as e:
                logger.warning("Error fetching agenda %s: %s", agenda_url, e)

            time.sleep(self.delay)

        logger.info("PrimeGov crawl complete: %d agendas fetched", len(self.results))
        return self.results

    # ── Internal methods ─────────────────────────────────────────────

    def _list_meetings(self) -> list[dict]:
        """List meetings via the PrimeGov API."""
        all_meetings: list[dict] = []

        if self.years:
            # Archived meetings for each requested year
            for y in self.years:
                url = f"{self.api_base}/api/v2/PublicPortal/ListArchivedMeetings?year={y}"
                meetings = self._api_get(url)
                if meetings:
                    logger.info("PrimeGov: year %s → %d meetings", y, len(meetings))
                    all_meetings.extend(meetings)
        else:
            # Upcoming meetings
            url = f"{self.api_base}/api/v2/PublicPortal/ListUpcomingMeetings"
            meetings = self._api_get(url)
            if meetings:
                all_meetings.extend(meetings)

        return all_meetings

    def _api_get(self, url: str) -> Optional[list]:
        """Make a GET request and return parsed JSON list."""
        try:
            resp = self.session.get(url, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                logger.warning("Unexpected API response type: %s", type(data))
            else:
                logger.warning("API returned HTTP %d for %s", resp.status_code, url)
        except (requests.RequestException, ValueError) as e:
            logger.error("API error for %s: %s", url, e)
        return None
