#!/usr/bin/env python3
"""
Integration test — runs the full classify + export pipeline on
realistic sample HTML that mimics Miami Beach government pages.
No network access needed.
"""

import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parsers.html_parser import parse_html
from parsers.pdf_parser import parse_pdf, ParsedChunk
from classifiers.relevance import load_sector_keywords, is_relevant
from classifiers.rules import (
    classify_procurement_stage, classify_signal_type, derive_lifecycle,
    infer_timeline, extract_amounts, select_best_amount, infer_friction,
    infer_signal_strength, infer_strategic_fit, extract_trigger_event,
    extract_agency, extract_geography, extract_meeting_date,
)
from classifiers.llm_enrichment import enrich_signal
from models.signal import Signal
from exporters.excel import export_excel, export_csv
from pipeline import load_yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("test")

# ══════════════════════════════════════════════════════════════════════════════
#  SAMPLE HTML PAGES (realistic Miami Beach content)
# ══════════════════════════════════════════════════════════════════════════════

SAMPLE_AGENDA_HTML = """
<html><head><title>City Commission Meeting Agenda - January 15, 2025</title></head>
<body>
<main>
<h1>City of Miami Beach — Regular City Commission Meeting</h1>
<p>January 15, 2025 — 9:00 AM — Commission Chambers</p>

<h2>CONSENT AGENDA</h2>
<h3>C4F — Stormwater Pump Station Rehabilitation</h3>
<p>A Resolution of the Mayor and City Commission authorizing the City Manager to
execute a contract with Infrastructure Solutions Inc. for the rehabilitation of
Stormwater Pump Station No. 12, located at 23rd Street and Collins Avenue,
in an amount not to exceed $2,750,000, funded from the Stormwater Utility Fund;
and further authorizing the City Manager to negotiate and execute any amendments
thereto. Project ID: CIP-SW-2025-012. Construction to begin summer 2025.</p>

<h3>C4G — Water Main Replacement Program Phase III</h3>
<p>A Resolution authorizing the issuance of ITB 2025-089-WF for Water Main
Replacement services in the North Beach area. The estimated project budget is
$4,200,000 from Water Fund reserves. Responses due March 15, 2025.</p>

<h3>C4H — Sanitary Sewer Force Main Emergency Repair</h3>
<p>A Resolution approving an emergency appropriation of $890,000 for the repair of
the 16-inch sanitary sewer force main along Alton Road between 5th and 10th Streets.
Work authorized under existing contract with Utility Constructors LLC.
Change order CO-2025-003 approved.</p>

<h2>DISCUSSION ITEMS</h2>
<h3>R5A — Neighborhood Improvement Stormwater Master Plan Update</h3>
<p>Discussion item: Presentation by the Public Works Department on the updated
Stormwater Master Plan for the Sunset Islands and La Gorce Island neighborhoods.
The plan addresses flooding concerns, sea level rise resiliency, and green
infrastructure improvements. The estimated five-year program cost is $18.5 million.
The commission will discuss prioritization and funding strategies. This item is
referred to the Finance and Economic Resiliency Committee for further review.</p>

<h3>R5B — Indian Creek Drive Drainage Improvements</h3>
<p>Update on the Indian Creek Drive drainage improvement project. Design is at
60% completion by Hazen and Sawyer. The project includes new stormwater outfall,
pump station upgrades, and backflow preventers. Total project cost: $6.3 million.
Construction authorization expected Q3 2025.</p>
</main></body></html>
"""

SAMPLE_PROCUREMENT_HTML = """
<html><head><title>Current Bid Opportunities — City of Miami Beach Procurement</title></head>
<body>
<main>
<h1>Current Bid Opportunities</h1>
<p>Effective January 1, 2025, Miami Beach Procurement has migrated to Bidnet Direct.</p>

<table>
<tr><th>Solicitation #</th><th>Title</th><th>Due Date</th><th>Category</th></tr>
<tr><td>ITB 2025-113-MP</td><td>Stormwater Drainage Improvements — West Avenue Corridor</td>
<td>February 28, 2025</td><td>Construction</td></tr>
<tr><td>RFQ 2025-045-WS</td><td>Professional Engineering Services for Water and Sewer Infrastructure</td>
<td>March 10, 2025</td><td>Professional Services</td></tr>
<tr><td>ITB 2025-098-GR</td><td>Citywide Parks and Recreation Facility Maintenance</td>
<td>March 20, 2025</td><td>Maintenance</td></tr>
</table>

<h2>Future Bid Opportunities</h2>
<table>
<tr><th>Forecast #</th><th>Description</th><th>Estimated Timeline</th><th>Est. Value</th></tr>
<tr><td>FBO-2025-SW-01</td><td>Sunset Islands Stormwater Pump Station Construction</td>
<td>FY 2026 Q1</td><td>$8,500,000</td></tr>
<tr><td>FBO-2025-WM-03</td><td>South Beach Water Distribution System Upgrades</td>
<td>FY 2026 Q2</td><td>$12,000,000</td></tr>
</table>
</main></body></html>
"""

SAMPLE_CIP_HTML = """
<html><head><title>Capital Improvement Program — City of Miami Beach</title></head>
<body>
<main>
<h1>Capital Improvement Program (CIP)</h1>
<p>The City of Miami Beach Capital Improvement Program includes approximately
50 active projects in planning, design, and construction phases, with significant
investments in water, sewer, and stormwater infrastructure funded through
utility revenues and bond proceeds.</p>

<h2>Stormwater Projects</h2>
<p><strong>Project: Sunset Harbor Stormwater Improvements Phase 2</strong><br>
Status: Under Construction<br>
Contractor: Ric-Man International<br>
Contract Value: $23,400,000<br>
Completion: December 2025<br>
This project includes installation of new pump stations, raised seawalls,
stormwater drainage upgrades, and road elevation along Purdy Avenue and
surrounding streets in the Sunset Harbor neighborhood.</p>

<p><strong>Project: La Gorce Island Drainage and Resiliency</strong><br>
Status: Design Phase (30% complete)<br>
Design Consultant: Hazen and Sawyer<br>
Estimated Construction Cost: $9,200,000<br>
This project addresses chronic flooding on La Gorce Island through new
stormwater pump stations, gravity drainage improvements, and tidal valve
installation. Environmental review is pending.</p>

<h2>Water & Sewer Projects</h2>
<p><strong>Project: North Beach Water Main Replacement</strong><br>
Status: Procurement — ITB advertised<br>
Estimated Value: $4,200,000<br>
Replacement of aging water mains in the North Beach area, including
69th to 87th Streets. ITB 2025-089-WF responses due March 15, 2025.</p>
</main></body></html>
"""

# ══════════════════════════════════════════════════════════════════════════════
#  RUN THE TEST
# ══════════════════════════════════════════════════════════════════════════════

def run_test():
    # Load sector config
    sectors_cfg = load_yaml("config/sectors.yaml")
    keyword_map = load_sector_keywords(sectors_cfg)
    selected_sectors = ["stormwater", "water", "sewer"]
    default_agency = "City of Miami Beach"
    default_geo = "Miami Beach, FL"

    test_pages = [
        ("Commission Agenda", SAMPLE_AGENDA_HTML,
         "https://www.miamibeachfl.gov/agenda/jan-15-2025/", "meetings"),
        ("Procurement", SAMPLE_PROCUREMENT_HTML,
         "https://www.miamibeachfl.gov/city-hall/procurement/bid-opportunities/", "procurement"),
        ("CIP", SAMPLE_CIP_HTML,
         "https://www.miamibeachfl.gov/city-hall/cip/", "cip"),
    ]

    all_signals: list[Signal] = []

    for page_label, html, url, category in test_pages:
        log.info("=" * 70)
        log.info("PARSING: %s", page_label)
        log.info("=" * 70)

        chunks = parse_html(html, url, category)
        log.info("  → %d chunks extracted", len(chunks))

        for i, chunk in enumerate(chunks):
            ok, best_sector, score = is_relevant(
                chunk.text, selected_sectors, keyword_map, threshold=0.04
            )
            if not ok:
                log.info("  [%d] SKIP (score=%.3f) %s", i, score, chunk.title[:60])
                continue

            log.info("  [%d] RELEVANT sector=%s score=%.3f  %s",
                     i, best_sector, score, chunk.title[:60])

            # Rule-based classification
            text = chunk.text
            proc_stage, proc_ev = classify_procurement_stage(text)
            sig_type = classify_signal_type(text, chunk.category)
            lifecycle = derive_lifecycle(proc_stage, sig_type)
            timeline = infer_timeline(text, proc_stage)
            amounts = extract_amounts(text)
            best_amt = select_best_amount(amounts)
            friction = infer_friction(text)
            trigger = extract_trigger_event(text)
            agency = extract_agency(text, default_agency)
            geo = extract_geography(text, default_geo)
            meeting_date = extract_meeting_date(text, chunk.date)
            strength = infer_signal_strength(text, proc_stage, bool(best_amt), bool(meeting_date))
            fit = infer_strategic_fit(score, proc_stage, strength)

            signal = Signal(
                signal_title=chunk.title[:120],
                agency=agency,
                geography=geo,
                sector=best_sector,
                estimated_value=best_amt,
                expected_timeline=timeline,
                meeting_date=meeting_date,
                signal_type=sig_type,
                procurement_stage=proc_stage,
                lifecycle_stage=lifecycle,
                signal_strength=strength,
                strategic_fit=fit,
                friction_level=friction,
                momentum="Unclear",
                trigger_event=trigger,
                strategic_notes=f"Detected from {page_label}. {proc_stage or 'Stage TBD'}.",
                source_link=url,
                evidence_snippet=(proc_ev or text[:200])[:500],
                evidence_page=chunk.page_number,
                confidence_score=round(score, 3),
                extraction_method="rule",
                raw_amounts=amounts,
                relevance_score=score,
            )
            all_signals.append(signal)

            log.info("       Stage=%-35s Type=%s", proc_stage, sig_type)
            log.info("       Value=%-20s Timeline=%s", best_amt, timeline)
            log.info("       Strength=%-10s Fit=%-15s Friction=%s", strength, fit, friction)
            log.info("       Trigger=%s", trigger[:80] if trigger else "(none)")

    # ── Phase 3: Project matching & merging ─────────────────────────
    log.info("=" * 70)
    log.info("PHASE 3: PROJECT MATCHING on %d raw signals", len(all_signals))
    log.info("=" * 70)

    from classifiers.project_tracker import track_and_merge
    from classifiers.project_matcher import group_signals_into_projects

    # First show raw project grouping
    projects = group_signals_into_projects(all_signals, threshold=0.45)
    for proj in projects:
        if len(proj.signals) > 1:
            log.info("  MERGED PROJECT: %s", proj.project_name[:60])
            for s in proj.signals:
                log.info("    ← %s [%s] from %s", s.signal_title[:50], s.procurement_stage, s.source_link.split("/")[-2] if "/" in s.source_link else "")

    # Merge
    merged_signals, projects = track_and_merge(all_signals, merge_threshold=0.45, keep_all=False)

    log.info("Result: %d raw → %d merged signals (%d projects)",
             len(all_signals), len(merged_signals), len(projects))

    # ── Export ────────────────────────────────────────────────────────
    log.info("=" * 70)
    log.info("EXPORTING %d merged signals", len(merged_signals))
    log.info("=" * 70)

    os.makedirs("data/output", exist_ok=True)
    xlsx_path = export_excel(merged_signals, "data/output/test_signals.xlsx", include_audit=True)
    csv_path = export_csv(merged_signals, "data/output/test_signals.csv")

    # Also export the un-merged version for comparison
    xlsx_raw = export_excel(all_signals, "data/output/test_signals_raw.xlsx", include_audit=True)

    log.info("Excel (merged): %s", xlsx_path)
    log.info("Excel (raw):    %s", xlsx_raw)
    log.info("CSV:            %s", csv_path)

    # ── Summary ───────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"  PHASE 3 RESULTS")
    print(f"  Raw signals: {len(all_signals)}  →  Merged: {len(merged_signals)}  ({len(projects)} projects)")
    print("=" * 70)
    for s in merged_signals:
        print(f"\n  [{s.signal_strength:>6}] [{s.sector:>12}] {s.signal_title[:65]}")
        print(f"          Stage: {s.procurement_stage}")
        print(f"          Value: {s.estimated_value or '—'}  |  Timeline: {s.expected_timeline or '—'}")
        print(f"          Momentum: {s.momentum:>13}  |  Friction: {s.friction_level}")
        print(f"          Method: {s.extraction_method}  |  Notes: {s.strategic_notes[:80]}")


if __name__ == "__main__":
    run_test()
