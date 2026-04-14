"""
Crawler — discovers candidate pages/files from seed URLs,
fetches HTML bodies and downloads PDF/DOCX files.
"""

from __future__ import annotations
import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Set
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Data containers ───────────────────────────────────────────────────────────

@dataclass
class CrawlResult:
    url: str
    final_url: str = ""
    category: str = ""          # meetings / procurement / cip / budget
    content_type: str = ""      # text/html, application/pdf, …
    html: str = ""
    local_path: str = ""        # for downloaded files
    title: str = ""
    depth: int = 0
    status_code: int = 0
    error: str = ""


# ── Crawler ───────────────────────────────────────────────────────────────────

class SiteCrawler:
    """Breadth-first crawler bounded by allowed_domains and max depth."""

    def __init__(self, site_config: dict, data_dir: str = "data/raw"):
        self.cfg = site_config
        self.allowed = set(site_config.get("allowed_domains", []))
        self.priority_pats = site_config.get("priority_patterns", [])
        self.ignore_pats = site_config.get("ignore_patterns", [])
        self.max_depth = site_config.get("max_depth", 3)
        self.max_pages = site_config.get("max_pages", 200)
        self.delay = site_config.get("request_delay_seconds", 1.5)
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            )
        })

        self.visited: Set[str] = set()
        self.results: List[CrawlResult] = []

    # ── Public API ────────────────────────────────────────────────────

    def crawl(self) -> List[CrawlResult]:
        """Run the full crawl starting from configured seeds."""
        queue: list[tuple[str, str, int]] = []     # (url, category, depth)
        for seed in self.cfg.get("seeds", []):
            url = seed["url"] if isinstance(seed, dict) else seed
            cat = seed.get("category", "") if isinstance(seed, dict) else ""
            queue.append((url, cat, 0))

        while queue and len(self.results) < self.max_pages:
            url, cat, depth = queue.pop(0)
            norm = self._normalize(url)
            if norm in self.visited:
                continue
            if not self._is_allowed(url):
                continue
            if self._should_ignore(url):
                continue

            self.visited.add(norm)
            result = self._fetch(url, cat, depth)
            if result.error:
                logger.warning("fetch error %s: %s", url, result.error)
                continue

            self.results.append(result)
            logger.info(
                "[%d/%d] depth=%d %s  (%s)",
                len(self.results), self.max_pages, depth, url,
                result.content_type[:30],
            )

            # Extract child links from HTML pages
            if "html" in result.content_type and depth < self.max_depth:
                child_links = self._extract_links(result.html, result.final_url)
                for link in child_links:
                    n = self._normalize(link)
                    if n not in self.visited:
                        child_cat = self._infer_category(link, cat)
                        # Prioritise high-value links
                        if self._is_priority(link):
                            queue.insert(0, (link, child_cat, depth + 1))
                        else:
                            queue.append((link, child_cat, depth + 1))

            time.sleep(self.delay)

        logger.info("Crawl complete: %d results", len(self.results))
        return self.results

    # ── Fetching ──────────────────────────────────────────────────────

    def _fetch(self, url: str, category: str, depth: int) -> CrawlResult:
        result = CrawlResult(url=url, category=category, depth=depth)
        try:
            resp = self.session.get(url, timeout=30, allow_redirects=True)
            result.status_code = resp.status_code
            result.final_url = resp.url
            result.content_type = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()

            if resp.status_code != 200:
                result.error = f"HTTP {resp.status_code}"
                return result

            if "html" in result.content_type:
                result.html = resp.text
                soup = BeautifulSoup(resp.text, "html.parser")
                tag = soup.find("title")
                result.title = tag.get_text(strip=True) if tag else ""

            elif result.content_type in (
                "application/pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/msword",
            ):
                ext = self._ext_for(result.content_type)
                fname = self._safe_filename(url, ext)
                local = os.path.join(self.data_dir, fname)
                with open(local, "wb") as f:
                    f.write(resp.content)
                result.local_path = local
                result.title = fname

            else:
                # Skip other content types
                result.error = f"skipped content-type {result.content_type}"

        except requests.RequestException as e:
            result.error = str(e)

        return result

    # ── Link extraction ───────────────────────────────────────────────

    def _extract_links(self, html: str, base_url: str) -> List[str]:
        soup = BeautifulSoup(html, "html.parser")
        links: list[str] = []
        for tag in soup.find_all("a", href=True):
            href = tag["href"].strip()
            if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            full = urljoin(base_url, href)
            # Strip fragment
            full = full.split("#")[0]
            if full and self._is_allowed(full):
                links.append(full)
        return links

    # ── Helpers ───────────────────────────────────────────────────────

    def _normalize(self, url: str) -> str:
        return url.rstrip("/").lower()

    def _is_allowed(self, url: str) -> bool:
        host = urlparse(url).hostname or ""
        return any(host.endswith(d) for d in self.allowed)

    def _is_priority(self, url: str) -> bool:
        lower = url.lower()
        return any(p.lower() in lower for p in self.priority_pats)

    def _should_ignore(self, url: str) -> bool:
        lower = url.lower()
        return any(p.lower() in lower for p in self.ignore_pats)

    def _infer_category(self, url: str, parent_cat: str) -> str:
        lower = url.lower()
        if any(k in lower for k in ("/procurement/", "/bid", "bidnet")):
            return "procurement"
        if any(k in lower for k in ("/cip/", "/capital")):
            return "cip"
        if any(k in lower for k in ("/budget/", "/finance/")):
            return "budget"
        if any(k in lower for k in ("/clerk/", "/meeting", "/agenda", "/minute", "novusagenda")):
            return "meetings"
        return parent_cat

    def _ext_for(self, ct: str) -> str:
        mapping = {
            "application/pdf": ".pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/msword": ".doc",
        }
        return mapping.get(ct, ".bin")

    def _safe_filename(self, url: str, ext: str) -> str:
        h = hashlib.md5(url.encode()).hexdigest()[:10]
        # Try to get a readable tail from the URL
        path = urlparse(url).path
        tail = os.path.basename(path) if path else ""
        if tail and "." in tail:
            return f"{h}_{tail}"
        return f"{h}{ext}"
