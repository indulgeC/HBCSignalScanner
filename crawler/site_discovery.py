"""
Site discovery — auto-generate a site config (allowed_domains, seeds,
crawler_mode, neighborhoods, etc.) from just a homepage URL.

Two modes:
  - heuristic only: free, deterministic, catches obvious URL/anchor patterns
  - LLM-assisted: uses Anthropic API to classify discovered links and
    pick the best seeds, including non-standard names like
    "Elevating Our Island Paradise" that pure regex would miss.
"""

from __future__ import annotations
import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# CMS detection: substrings to look for in homepage HTML
CMS_SIGNATURES = {
    "primegov":   ["primegov.com"],
    "granicus":   ["granicus.com"],
    "legistar":   ["legistar.com"],
    "novus":      ["novusagenda.com"],
    "civicclerk": ["civicclerk.com"],
    "civicplus":  ["civicplus.com", "civicengage"],
}

# Heuristic keywords per category, matched against URL + anchor text
SEED_KEYWORDS = {
    "meetings": [
        "agenda", "meeting", "minute", "/clerk", "/council", "/commission",
    ],
    "procurement": [
        "procurement", "/bid", "/rfp", "/rfq", "/itb", "solicitation",
        "vendor", "purchasing",
    ],
    "cip": [
        "/cip", "capital", "improvement", "public_works", "public-works",
        "infrastructure", "/projects", "/program", "resilien", "stormwater",
        "utilit",
    ],
    "budget": [
        "/budget", "/finance", "/cafr", "fiscal",
    ],
}

# Default ignore_patterns suitable for most municipal sites
DEFAULT_IGNORE_PATTERNS = [
    "/parks", "/recreation", "/library/",
    "/jobs", "/careers", "/employment", "/hr/",
    "/news/", "/events/", "/calendar.php",
    "/visitors", "/tourism",
    "/police", "/fire-rescue", "/ems",
    ".jpg", ".jpeg", ".png", ".gif", ".css", ".js", ".ico", ".woff",
]


@dataclass
class DiscoveredSite:
    name: str = ""
    display_name: str = ""
    base_url: str = ""
    default_agency: str = ""
    default_geography: str = ""
    crawler_mode: str = "default"
    primegov_api_base: str = ""
    allowed_domains: List[str] = field(default_factory=list)
    seeds: List[dict] = field(default_factory=list)
    priority_patterns: List[str] = field(default_factory=list)
    ignore_patterns: List[str] = field(default_factory=list)
    neighborhoods: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    used_llm: bool = False

    def to_yaml_dict(self) -> dict:
        d = {
            "name": self.name,
            "display_name": self.display_name,
            "base_url": self.base_url,
            "default_agency": self.default_agency,
            "default_geography": self.default_geography,
        }
        if self.neighborhoods:
            d["neighborhoods"] = self.neighborhoods
        d["crawler_mode"] = self.crawler_mode
        if self.crawler_mode == "primegov" and self.primegov_api_base:
            d["primegov_api_base"] = self.primegov_api_base
        d["allowed_domains"] = self.allowed_domains
        d["seeds"] = self.seeds
        d["priority_patterns"] = self.priority_patterns
        d["ignore_patterns"] = self.ignore_patterns
        d["max_depth"] = 3
        d["max_pages"] = 200
        d["request_delay_seconds"] = 1.5
        return d


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def discover_site(
    url: str,
    use_llm: bool = False,
    api_key: str = "",
    llm_model: str = "",
) -> DiscoveredSite:
    """Auto-generate a site config from a homepage URL.

    Falls back to heuristics if `use_llm=False`, `api_key` is empty,
    or the LLM call fails. Always returns a usable config; check
    `result.notes` for human-readable status messages.
    """
    notes: List[str] = []

    # Normalize URL
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    # Fetch homepage
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        resp = session.get(base_url, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        html = resp.text
        actual = urlparse(resp.url)
        actual_base = f"{actual.scheme}://{actual.netloc}"
        if actual_base != base_url:
            notes.append(f"Redirected to {actual_base}")
            base_url = actual_base
            parsed = actual
    except requests.RequestException as e:
        raise RuntimeError(f"Could not fetch {base_url}: {e}") from e

    soup = BeautifulSoup(html, "html.parser")

    # Title → agency / geography. Some sites have unhelpful titles like
    # just "Home" — fall back to meta description and H1 in that case.
    title_tag = soup.find("title")
    page_title = title_tag.get_text(strip=True) if title_tag else ""

    text_sources = [page_title]
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        text_sources.append(meta_desc["content"][:300])
    h1_tag = soup.find("h1")
    if h1_tag:
        text_sources.append(h1_tag.get_text(strip=True)[:200])

    name = _derive_site_name(parsed.netloc)
    display_name, agency, geography = "", "", ""
    for src in text_sources:
        d, a, g = _derive_agency_geography(src, parsed.netloc)
        # Accept the first source that produced a real agency string
        # (one that actually starts with City/Village/Town/County/etc).
        if re.match(
            r"^(?:City|Village|Town|County|Borough|Municipality)\s+of\s+",
            a or "", re.I,
        ):
            display_name, agency, geography = d, a, g
            break
    if not agency:
        # Nothing matched — keep whatever the title-based parse produced
        display_name, agency, geography = _derive_agency_geography(page_title, parsed.netloc)
    allowed_domains = _derive_allowed_domains(parsed.netloc)

    # CMS detection
    crawler_mode, primegov_api_base = _detect_cms(html)
    if crawler_mode == "primegov":
        notes.append(f"Detected PrimeGov CMS at {primegov_api_base or '(subdomain not found)'}")
        if primegov_api_base:
            host = urlparse(primegov_api_base).hostname or ""
            if host and host not in allowed_domains:
                allowed_domains.append(host)
    elif crawler_mode != "default":
        notes.append(f"Detected CMS hint: {crawler_mode} (using default crawler)")

    # Candidate links from homepage + sitemap
    candidates = _extract_candidates(soup, base_url, parsed.netloc)
    sitemap_urls = _try_sitemap(session, base_url)
    if sitemap_urls:
        notes.append(f"Found sitemap with {len(sitemap_urls)} URLs")
        seen = {c[0] for c in candidates}
        for u in sitemap_urls:
            if u not in seen:
                candidates.append((u, ""))
                seen.add(u)
    notes.append(f"Discovered {len(candidates)} candidate links")

    # Pick seeds
    seeds: List[dict] = []
    neighborhoods: List[str] = []
    priority_patterns: List[str] = []
    ignore_patterns: List[str] = list(DEFAULT_IGNORE_PATTERNS)
    used_llm = False

    if use_llm and api_key:
        try:
            llm_result = _discover_via_llm(
                base_url=base_url,
                page_title=page_title,
                candidates=candidates[:120],
                api_key=api_key,
                model=llm_model,
            )
            seeds = llm_result.get("seeds", []) or []
            display_name = llm_result.get("display_name") or display_name
            agency = llm_result.get("default_agency") or agency
            geography = llm_result.get("default_geography") or geography
            neighborhoods = llm_result.get("neighborhoods", []) or []
            priority_patterns = llm_result.get("priority_patterns", []) or []
            llm_ignore = llm_result.get("ignore_patterns", []) or []
            if llm_ignore:
                ignore_patterns = llm_ignore
            used_llm = True
            notes.append(f"AI selected {len(seeds)} seeds from {len(candidates)} candidates")
        except Exception as e:
            logger.warning("LLM discovery failed (%s); falling back to heuristics", e)
            notes.append(f"AI discovery failed ({type(e).__name__}); using heuristics")
    elif use_llm and not api_key:
        notes.append("AI discovery skipped: no API key — using heuristics")

    if not used_llm:
        seeds = _classify_seeds_heuristic(candidates)
        priority_patterns = _derive_priority_patterns()

    # Many sites embed PrimeGov in an iframe on the meetings sub-page rather
    # than the homepage. If we picked a meetings seed, peek at it to look
    # for a PrimeGov hint.
    if crawler_mode == "default" and seeds:
        meetings_seed = next(
            (s for s in seeds if s.get("category") == "meetings"),
            None,
        )
        if meetings_seed:
            try:
                r = session.get(meetings_seed["url"], timeout=10)
                if r.status_code == 200:
                    sub_mode, sub_pg = _detect_cms(r.text)
                    if sub_mode == "primegov":
                        crawler_mode = "primegov"
                        primegov_api_base = sub_pg
                        if sub_pg:
                            sub_host = urlparse(sub_pg).hostname or ""
                            if sub_host and sub_host not in allowed_domains:
                                allowed_domains.append(sub_host)
                        notes.append(
                            f"PrimeGov detected on meetings page → {sub_pg or '(subdomain not extracted)'}"
                        )
            except requests.RequestException as e:
                logger.debug("Sub-page CMS probe failed: %s", e)

    if not seeds:
        notes.append("⚠ No seeds detected — add them manually before saving")

    return DiscoveredSite(
        name=name,
        display_name=display_name,
        base_url=base_url,
        default_agency=agency,
        default_geography=geography,
        crawler_mode=crawler_mode,
        primegov_api_base=primegov_api_base,
        allowed_domains=allowed_domains,
        seeds=seeds,
        priority_patterns=priority_patterns,
        ignore_patterns=ignore_patterns,
        neighborhoods=neighborhoods,
        notes=notes,
        used_llm=used_llm,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  HEURISTIC HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _derive_site_name(netloc: str) -> str:
    """Turn 'www.keybiscayne.fl.gov' → 'key_biscayne'."""
    host = netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    base = host.split(".")[0]
    base = re.sub(r"-+", "_", base)
    # Camel-case to snake_case (rare in domains but handle it)
    base = re.sub(r"([a-z])([A-Z])", r"\1_\2", base).lower()
    return base


def _derive_agency_geography(title: str, netloc: str) -> Tuple[str, str, str]:
    """Best-effort (display_name, agency, geography) from page title.

    Examples we want to handle:
      'Village of Key Biscayne, FL'
      'City of Miami Beach, FL | Home'
      'Welcome to City of Surfside'
    """
    if not title:
        return netloc, netloc, ""

    # Match "City of <1-4 capitalized words>" optionally followed by ", ST".
    # Capitalized-words pattern avoids the over-eager hyphen/space issue
    # we'd hit with a generic [\w\s\-\.]+? approach on prose like
    # "Town of Surfside is a beautiful pedestrian-friendly community".
    m = re.search(
        r"((?:City|Village|Town|County|Borough|Municipality)\s+of\s+"
        r"(?:[A-Z][\w\.]*\s+){0,3}[A-Z][\w\.]*)"
        r"(?:,\s*([A-Z]{2}))?",
        title,
    )
    if m:
        agency = m.group(1).strip()
        state = (m.group(2) or "").strip()
        # Strip common trailing junk that title designers append
        # ("City of Miami Beach Home", "Village of X Welcome", etc.)
        agency = re.sub(
            r"\s+(?:Home|Welcome|Site|Government|Official|Main|Page)$",
            "", agency, flags=re.I,
        ).strip()
        city_part = re.sub(
            r"^(?:City|Village|Town|County|Borough|Municipality)\s+of\s+",
            "", agency, flags=re.I,
        ).strip()
        geography = f"{city_part}, {state}".strip(", ") if state else city_part
        return agency, agency, geography

    fallback = re.split(r"[|·\-–]", title)[0].strip()
    return fallback, fallback, ""


def _derive_allowed_domains(netloc: str) -> List[str]:
    host = netloc.lower()
    bare = host[4:] if host.startswith("www.") else host
    out = []
    for d in (bare, "www." + bare, "files." + bare):
        if d not in out:
            out.append(d)
    return out


def _detect_cms(html: str) -> Tuple[str, str]:
    """Return (crawler_mode, primegov_api_base)."""
    html_lower = html.lower()

    if "primegov.com" in html_lower:
        m = re.search(r"https?://([\w-]+\.primegov\.com)", html, re.I)
        if m:
            return "primegov", f"https://{m.group(1).lower()}"
        return "primegov", ""

    for cms, sigs in CMS_SIGNATURES.items():
        if cms == "primegov":
            continue
        if any(s in html_lower for s in sigs):
            return cms, ""

    return "default", ""


def _extract_candidates(
    soup: BeautifulSoup, base_url: str, host: str,
) -> List[Tuple[str, str]]:
    """Same-host (url, anchor_text) pairs from homepage."""
    seen = set()
    out: List[Tuple[str, str]] = []
    bare_host = host[4:] if host.startswith("www.") else host
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        full = urljoin(base_url, href).split("#")[0]
        if not full.startswith(("http://", "https://")):
            continue
        h = urlparse(full).hostname or ""
        if not (h == host or h == bare_host or h.endswith("." + bare_host)):
            continue
        if any(full.lower().endswith(ext) for ext in (
            ".jpg", ".jpeg", ".png", ".gif", ".css", ".js",
            ".ico", ".woff", ".woff2",
        )):
            continue
        text = a.get_text(strip=True)[:120]
        if full in seen:
            continue
        seen.add(full)
        out.append((full, text))
    return out


def _try_sitemap(session: requests.Session, base_url: str) -> List[str]:
    """Return URLs found in sitemap.xml (or via robots.txt Sitemap directive)."""
    candidates = [
        f"{base_url}/sitemap.xml",
        f"{base_url}/sitemap_index.xml",
        f"{base_url}/sitemap-index.xml",
    ]
    try:
        r = session.get(f"{base_url}/robots.txt", timeout=10)
        if r.status_code == 200:
            for m in re.finditer(r"^\s*Sitemap:\s*(\S+)", r.text, re.I | re.M):
                candidates.append(m.group(1).strip())
    except requests.RequestException:
        pass

    urls: List[str] = []
    for sm in candidates:
        try:
            r = session.get(sm, timeout=10)
            if r.status_code == 200 and "<loc>" in r.text:
                urls.extend(re.findall(r"<loc>\s*(.*?)\s*</loc>", r.text))
                if urls:
                    break
        except requests.RequestException:
            continue

    return urls[:300]


def _classify_seeds_heuristic(
    candidates: List[Tuple[str, str]],
) -> List[dict]:
    """Pick the best 1-2 seeds per category using URL + anchor keywords."""
    by_category: dict[str, list[tuple[float, str, str]]] = {
        cat: [] for cat in SEED_KEYWORDS
    }

    for url, text in candidates:
        url_lower = url.lower()
        text_lower = text.lower()
        for cat, keywords in SEED_KEYWORDS.items():
            score = 0.0
            for kw in keywords:
                if kw in url_lower:
                    score += 2.0
                if kw in text_lower:
                    score += 1.0
            if score > 0:
                # Prefer index/landing pages (shorter paths, ending in /index.*)
                path = urlparse(url).path
                depth = max(path.count("/") - 1, 0)
                bonus = 0
                if path.endswith(("/", "/index.php", "/index.html", "/index.aspx")):
                    bonus = 2
                final = score * 10 - depth + bonus
                by_category[cat].append((final, url, text))

    seeds: List[dict] = []
    seen_urls = set()
    for cat, hits in by_category.items():
        hits.sort(reverse=True)
        for _, url, text in hits[:2]:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            seeds.append({
                "url": url,
                "category": cat,
                "label": (text or cat.title())[:80],
            })
    return seeds


def _derive_priority_patterns() -> List[str]:
    """Standard set of priority patterns suitable for most municipal sites."""
    return [
        "/agenda", "/meeting", "/minute", "/clerk",
        "/procurement", "/bid", "/rfp", "/rfq",
        "/cip", "/capital", "/public_works", "/public-works",
        "/budget", "/finance",
        "primegov.com",
    ]


# ══════════════════════════════════════════════════════════════════════════════
#  LLM HELPER
# ══════════════════════════════════════════════════════════════════════════════

def _discover_via_llm(
    base_url: str,
    page_title: str,
    candidates: List[Tuple[str, str]],
    api_key: str,
    model: str = "",
) -> dict:
    """Ask Claude to classify candidates and pick the best seeds.

    Returns a dict with keys: display_name, default_agency, default_geography,
    neighborhoods, seeds, priority_patterns, ignore_patterns.
    """
    import anthropic
    from classifiers.llm_enrichment import DEFAULT_MODEL

    use_model = model or DEFAULT_MODEL
    client = anthropic.Anthropic(api_key=api_key)

    candidates_text = "\n".join(
        f"- {url}{' | ' + text if text else ''}"
        for url, text in candidates
    )

    prompt = f"""You are configuring a web crawler for a government infrastructure-procurement signal scanner.

Given a government website's homepage and discovered links, return a JSON config that helps the scanner find:
  - Council / commission meeting agendas and minutes
  - Procurement / bid postings (RFP, RFQ, ITB)
  - Capital improvement program (CIP) projects, public-works pages
  - Budget and finance documents

Site URL: {base_url}
Page title: {page_title}

Discovered links (URL | anchor text):
{candidates_text}

Return ONLY a JSON object with these keys (no markdown fences):

{{
  "display_name": "Human-readable agency name (e.g. 'Village of Key Biscayne')",
  "default_agency": "Agency name as it appears in documents (e.g. 'Village of Key Biscayne')",
  "default_geography": "City, State (e.g. 'Key Biscayne, FL')",
  "neighborhoods": ["list", "of", "named sub-areas / districts / project zones"],
  "seeds": [
    {{"url": "<full URL>", "category": "meetings|procurement|cip|budget", "label": "Short label"}}
  ],
  "priority_patterns": ["URL substrings indicating high-value pages"],
  "ignore_patterns": ["/parks", "/careers", "/news"]
}}

Rules:
  - Pick 4-8 seeds total, one or two per category. Prefer LANDING/INDEX pages over deep individual items.
  - For municipalities with NAMED capital programs (e.g. "Elevating Our Island Paradise", "Build Better Buffalo", "Rising Above"), include the program library/portal as a "cip" seed even if its URL doesn't contain /cip/.
  - neighborhoods: max 6, only include if the site mentions specific named sub-areas (Harbor Park, Garden District, South Beach, Zone 1, etc.). Empty list is fine.
  - priority_patterns and ignore_patterns: max 12 each.
  - Return valid JSON only, no explanation."""

    response = client.messages.create(
        model=use_model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)
