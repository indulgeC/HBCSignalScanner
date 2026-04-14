"""
Pipeline — orchestrates the full flow:
  crawl → parse → relevance filter → rule classify → LLM enrich → export
"""

from __future__ import annotations
import logging
import os
from typing import Callable, List, Optional

import yaml

from crawler.discover import SiteCrawler, CrawlResult
from crawler.primegov import PrimeGovCrawler
from parsers.html_parser import parse_html, ParsedChunk
from parsers.pdf_parser import parse_pdf
from classifiers.relevance import load_sector_keywords, is_relevant
from classifiers.rules import (
    classify_procurement_stage,
    classify_signal_type,
    derive_lifecycle,
    infer_timeline,
    extract_amounts,
    select_best_amount,
    infer_friction,
    infer_signal_strength,
    infer_strategic_fit,
    extract_trigger_event,
    extract_agency,
    extract_geography,
    extract_meeting_date,
)
from classifiers.llm_enrichment import enrich_signal
from classifiers.project_tracker import track_and_merge
from models.signal import Signal
from exporters.excel import export_excel, export_csv

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG LOADING
# ══════════════════════════════════════════════════════════════════════════════

def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_site_config(site_name: str, config_dir: str = "config") -> dict:
    path = os.path.join(config_dir, "sites", f"{site_name}.yaml")
    return load_yaml(path)


def load_sectors_config(config_dir: str = "config") -> dict:
    return load_yaml(os.path.join(config_dir, "sectors.yaml"))


# ══════════════════════════════════════════════════════════════════════════════
#  PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

class SignalPipeline:
    """End-to-end pipeline: sites × sectors → signals → Excel."""

    def __init__(
        self,
        site_names: List[str],
        sectors: List[str],
        config_dir: str = "config",
        data_dir: str = "data",
        use_llm: bool = True,
        relevance_threshold: float = 0.05,
        max_pages: Optional[int] = None,
        merge_projects: bool = True,
        year: Optional[int] = None,
        years: Optional[List[int]] = None,
        llm_api_key: str = "",
        llm_model: str = "",
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ):
        self.site_names = site_names
        self.sectors = sectors
        self.config_dir = config_dir
        self.data_dir = data_dir
        self.use_llm = use_llm
        self.relevance_threshold = relevance_threshold
        self.max_pages_override = max_pages
        self.merge_projects = merge_projects
        # Normalize year/years into a single list (or None = upcoming only)
        if years:
            self.years = list(years)
        elif year:
            self.years = [year]
        else:
            self.years = None
        self.llm_api_key = llm_api_key
        self.llm_model = llm_model
        self.progress_callback = progress_callback

        self.sectors_config = load_sectors_config(config_dir)
        self.keyword_map = load_sector_keywords(self.sectors_config)

        self.signals: List[Signal] = []
        self.projects: list = []

    # ── Progress reporting ────────────────────────────────────────────

    def _progress(self, pct: float, msg: str):
        """Report progress to an optional UI callback."""
        if self.progress_callback:
            try:
                self.progress_callback(max(0.0, min(1.0, pct)), msg)
            except Exception as e:
                logger.debug("progress_callback error: %s", e)

    # ── Main entry point ──────────────────────────────────────────────

    def run(self, output_path: str = "data/output/signals.xlsx") -> str:
        """Execute the full pipeline and return the output file path."""
        logger.info(
            "Pipeline starting — sites=%s  sectors=%s",
            self.site_names, self.sectors,
        )
        self._progress(0.02, "Starting pipeline...")

        n_sites = max(len(self.site_names), 1)
        site_budget = 0.85 / n_sites   # sites occupy 2% – 87% of progress

        for i, site_name in enumerate(self.site_names):
            base = 0.02 + i * site_budget
            self._process_site(site_name, base_pct=base, budget=site_budget)

        # ── Phase 3: Project matching, merging, momentum ─────────────
        self._progress(0.88, f"Merging {len(self.signals)} signals into projects...")
        if self.merge_projects:
            logger.info("Phase 3: grouping %d raw signals into projects...", len(self.signals))
            self.signals, self.projects = track_and_merge(
                self.signals,
                merge_threshold=0.45,
                keep_all=False,   # one row per project
            )
            logger.info("Phase 3 complete: %d merged signals", len(self.signals))
        else:
            # Still compute momentum/friction but keep all rows
            logger.info("Phase 3 (no merge): computing momentum for %d signals...", len(self.signals))
            self.signals, self.projects = track_and_merge(
                self.signals,
                merge_threshold=0.45,
                keep_all=True,
            )

        # Sort by signal strength then relevance
        strength_order = {"High": 0, "Medium": 1, "Low": 2, "": 3}
        self.signals.sort(key=lambda s: (
            strength_order.get(s.signal_strength, 3),
            -s.relevance_score,
        ))

        # Export
        self._progress(0.95, f"Exporting {len(self.signals)} signals to Excel...")
        export_excel(self.signals, output_path, include_audit=True)
        csv_path = output_path.rsplit(".", 1)[0] + ".csv"
        export_csv(self.signals, csv_path)

        logger.info("Pipeline complete — %d signals", len(self.signals))
        self._progress(1.0, f"Done — {len(self.signals)} signals")
        return output_path

    # ── Per-site processing ───────────────────────────────────────────

    def _process_site(self, site_name: str, base_pct: float = 0.0, budget: float = 1.0):
        """Process one site. Within this site's progress budget:
           crawl = 70%, parse = 5%, filter = 5%, classify = 20%.
        """
        site_cfg = load_site_config(site_name, self.config_dir)
        if self.max_pages_override:
            site_cfg["max_pages"] = self.max_pages_override

        default_agency = site_cfg.get("default_agency", "")
        default_geo = site_cfg.get("default_geography", "")

        self._progress(base_pct, f"[{site_name}] Starting crawl...")

        # ── 1. CRAWL ─────────────────────────────────────────────────
        raw_dir = os.path.join(self.data_dir, "raw", site_name)
        crawler_mode = site_cfg.get("crawler_mode", "default")

        # Sub-callback: map crawler's 0–1 progress into this site's crawl slice
        def crawl_cb(local_pct: float, msg: str):
            overall = base_pct + budget * 0.70 * max(0.0, min(1.0, local_pct))
            self._progress(overall, f"[{site_name}] {msg}")

        if crawler_mode == "primegov":
            crawler = PrimeGovCrawler(
                site_cfg,
                years=self.years,
                max_pages=self.max_pages_override,
                progress_callback=crawl_cb,
            )
        else:
            crawler = SiteCrawler(
                site_cfg,
                data_dir=raw_dir,
                progress_callback=crawl_cb,
            )

        crawl_results = crawler.crawl()
        logger.info("Site %s: crawled %d pages/files (mode=%s)", site_name, len(crawl_results), crawler_mode)

        # ── 2. PARSE & SPLIT ─────────────────────────────────────────
        self._progress(
            base_pct + budget * 0.70,
            f"[{site_name}] Parsing {len(crawl_results)} pages...",
        )
        all_chunks: list[tuple[ParsedChunk, CrawlResult]] = []
        for cr in crawl_results:
            chunks = self._parse_result(cr)
            for chunk in chunks:
                all_chunks.append((chunk, cr))

        logger.info("Site %s: %d chunks extracted", site_name, len(all_chunks))

        # ── 3. FILTER by relevance ───────────────────────────────────
        self._progress(
            base_pct + budget * 0.75,
            f"[{site_name}] Filtering {len(all_chunks)} chunks by relevance...",
        )
        relevant: list[tuple[ParsedChunk, CrawlResult, str, float]] = []
        for chunk, cr in all_chunks:
            ok, best_sector, score = is_relevant(
                chunk.text, self.sectors, self.keyword_map,
                threshold=self.relevance_threshold,
            )
            if ok:
                relevant.append((chunk, cr, best_sector, score))

        logger.info("Site %s: %d relevant chunks (threshold=%.2f)",
                     site_name, len(relevant), self.relevance_threshold)

        # ── 4. CLASSIFY + ENRICH each chunk ──────────────────────────
        n_rel = len(relevant)
        classify_label = "Classifying + AI enriching" if self.use_llm else "Classifying"
        self._progress(
            base_pct + budget * 0.80,
            f"[{site_name}] {classify_label} {n_rel} signals...",
        )
        for i, (chunk, cr, sector, rel_score) in enumerate(relevant):
            signal = self._build_signal(chunk, cr, sector, rel_score, default_agency, default_geo)
            self.signals.append(signal)
            # Update progress periodically (every few items) to avoid UI spam
            if n_rel > 0 and (i % 3 == 0 or i == n_rel - 1):
                local = 0.80 + 0.20 * ((i + 1) / n_rel)
                self._progress(
                    base_pct + budget * local,
                    f"[{site_name}] {classify_label} {i+1}/{n_rel}...",
                )

    # ── Parse dispatcher ──────────────────────────────────────────────

    def _parse_result(self, cr: CrawlResult) -> List[ParsedChunk]:
        if cr.html:
            return parse_html(cr.html, cr.final_url or cr.url, cr.category)
        elif cr.local_path and cr.local_path.endswith(".pdf"):
            return parse_pdf(cr.local_path, cr.url, cr.category)
        # Future: .docx support
        return []

    # ── Build a single signal from a chunk ────────────────────────────

    def _build_signal(
        self,
        chunk: ParsedChunk,
        cr: CrawlResult,
        sector: str,
        rel_score: float,
        default_agency: str,
        default_geo: str,
    ) -> Signal:
        text = chunk.text

        # ── Rule-based extraction ────────────────────────────────────
        proc_stage, proc_evidence = classify_procurement_stage(text)
        sig_type = classify_signal_type(text, chunk.category)
        lifecycle = derive_lifecycle(proc_stage, sig_type)
        timeline = infer_timeline(text, proc_stage)
        amounts = extract_amounts(text)
        best_amount = select_best_amount(amounts)
        friction = infer_friction(text)
        trigger = extract_trigger_event(text)
        agency = extract_agency(text, default_agency)
        geography = extract_geography(text, default_geo)
        meeting_date = extract_meeting_date(text, chunk.date)

        has_amount = bool(best_amount)
        has_date = bool(meeting_date)
        strength = infer_signal_strength(text, proc_stage, has_amount, has_date)
        fit = infer_strategic_fit(rel_score, proc_stage, strength)

        # ── LLM enrichment (optional) ────────────────────────────────
        enriched = {}
        if self.use_llm:
            enriched = enrich_signal(
                text=text,
                agency=agency,
                sector=sector,
                procurement_stage=proc_stage,
                signal_type=sig_type,
                amounts=amounts,
                rule_strength=strength,
                rule_fit=fit,
                api_key=self.llm_api_key,
                model=self.llm_model,
            )

        # ── Assemble signal ──────────────────────────────────────────
        signal = Signal(
            signal_title=enriched.get("signal_title", chunk.title[:120]),
            agency=agency,
            geography=geography,
            sector=sector,
            estimated_value=enriched.get("estimated_value", best_amount),
            expected_timeline=timeline,
            meeting_date=meeting_date,
            signal_type=sig_type,
            procurement_stage=proc_stage,
            lifecycle_stage=lifecycle,
            signal_strength=enriched.get("signal_strength", strength),
            strategic_fit=enriched.get("strategic_fit", fit),
            friction_level=friction,
            momentum="Unclear",   # requires project history (Phase 3)
            trigger_event=trigger,
            strategic_notes=enriched.get("strategic_notes", ""),
            source_link=cr.final_url or cr.url,
            source_file_url=cr.local_path if cr.local_path else "",
            source_page_url=cr.url,
            evidence_snippet=(proc_evidence or text[:200])[:500],
            evidence_page=chunk.page_number,
            confidence_score=round(rel_score, 3),
            extraction_method="ai" if enriched else "rule",
            raw_amounts=amounts,
            doc_url=cr.url,
            doc_type=chunk.doc_type,
            chunk_index=0,
            relevance_score=rel_score,
        )
        return signal

    # ── Deduplication ─────────────────────────────────────────────────

    def _dedupe(self, signals: List[Signal]) -> List[Signal]:
        """Remove near-duplicate signals based on source + title similarity."""
        seen: dict[str, Signal] = {}
        deduped: list[Signal] = []

        for sig in signals:
            key = f"{sig.source_link}::{sig.signal_title[:60].lower()}"
            if key not in seen:
                seen[key] = sig
                deduped.append(sig)
            else:
                # Keep the one with higher confidence
                existing = seen[key]
                if sig.confidence_score > existing.confidence_score:
                    deduped.remove(existing)
                    seen[key] = sig
                    deduped.append(sig)

        logger.info("Dedup: %d → %d", len(signals), len(deduped))
        return deduped
