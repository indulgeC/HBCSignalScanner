#!/usr/bin/env python3
"""
Integration test — feeds realistic sample data through the full
parse → relevance → classify → enrich → export pipeline.

Run:  python test_pipeline.py
"""

import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models.signal import Signal
from parsers.html_parser import parse_html, ParsedChunk
from parsers.pdf_parser import parse_pdf
from classifiers.relevance import load_sector_keywords, is_relevant, score_relevance
from classifiers.rules import (
    classify_procurement_stage, classify_signal_type, derive_lifecycle,
    infer_timeline, extract_amounts, select_best_amount, infer_friction,
    infer_signal_strength, infer_strategic_fit, extract_trigger_event,
    extract_agency, extract_geography, extract_meeting_date,
)
from classifiers.llm_enrichment import enrich_signal
from exporters.excel import export_excel, export_csv
from pipeline import load_yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
#  SAMPLE DATA — simulates what the crawler would return from Miami Beach
# ══════════════════════════════════════════════════════════════════════════════

SAMPLE_HTML_AGENDA = """
<html><head><title>City Commission Meeting Agenda - January 15, 2026</title></head>
<body>
<main>
<h1>City of Miami Beach - Regular City Commission Meeting</h1>
<p>January 15, 2026 — 9:00 AM — Commission Chamber</p>

<h2>Consent Agenda</h2>

<h3>C4A - Stormwater Pump Station Improvements Phase II</h3>
<p>A RESOLUTION OF THE MAYOR AND CITY COMMISSION OF THE CITY OF MIAMI BEACH, FLORIDA,
AUTHORIZING THE CITY MANAGER TO EXECUTE A PROFESSIONAL SERVICES AGREEMENT WITH AECOM
TECHNICAL SERVICES, INC. FOR DESIGN SERVICES RELATED TO THE STORMWATER PUMP STATION
IMPROVEMENTS — PHASE II PROJECT, IN AN AMOUNT NOT TO EXCEED $2,450,000.00; AND
FURTHER AUTHORIZING THE CITY MANAGER TO NEGOTIATE AND EXECUTE ALL AMENDMENTS THERETO.</p>
<p>Project ID: CIP-SW-2026-014. This project includes the design of improvements to
seven (7) stormwater pump stations located in the North Beach and Mid-Beach neighborhoods.
Construction is anticipated to begin in Fall 2027.</p>

<h3>C4B - Water Main Replacement Program — 41st Street Corridor</h3>
<p>A RESOLUTION AUTHORIZING THE CITY MANAGER TO EXECUTE CHANGE ORDER NO. 2 TO THE
CONTRACT WITH LANZO CONSTRUCTION COMPANY, FOR THE 41ST STREET WATER MAIN REPLACEMENT
PROJECT, IN THE AMOUNT OF $385,000.00, FOR A REVISED TOTAL CONTRACT AMOUNT OF
$4,125,000.00.</p>
<p>This change order addresses unforeseen utility conflicts and additional pipe
lining work required on the 41st Street corridor. Work is currently underway with
expected completion by June 2026.</p>

<h3>C4C - Annual Sewer Rehabilitation Contract</h3>
<p>DISCUSSION ITEM — Presentation by Public Works Department on the status of the
FY 2026 Sanitary Sewer Rehabilitation Program. The program includes trenchless sewer
line rehabilitation of approximately 15,000 linear feet of aging sanitary sewer mains
throughout the city. Staff is requesting direction to prepare ITB specifications for
the FY 2027 continuation phase.</p>

<h3>R5A - Indian Creek Drive Drainage Improvements</h3>
<p>A RESOLUTION OF THE MAYOR AND CITY COMMISSION ACCEPTING THE RECOMMENDATION OF THE
FINANCE AND ECONOMIC RESILIENCY COMMITTEE TO APPROVE THE APPROPRIATION OF $8,500,000
FROM STORMWATER UTILITY FUND RESERVES FOR THE INDIAN CREEK DRIVE DRAINAGE AND ROADWAY
IMPROVEMENTS PROJECT. The project will address chronic flooding in the Indian Creek
Drive corridor between 25th and 41st Streets. Design is 60% complete. ITB 2026-113-MP
is anticipated for advertisement in Q3 FY 2026.</p>

<h3>C5D - Citywide Resiliency Assessment Update</h3>
<p>INFORMATION ITEM — Update from the Environment and Sustainability Department on the
Citywide Stormwater Resiliency and Sea Level Rise Assessment. The assessment evaluates
the capacity of the existing stormwater infrastructure to handle projected increases
in rainfall intensity and sea level rise through 2060. No action requested at this time.</p>

<h3>R7B - Emergency Services Communications Upgrade</h3>
<p>A RESOLUTION AUTHORIZING THE CITY MANAGER TO ISSUE ITB 2026-201-WG FOR THE
PROCUREMENT OF EMERGENCY COMMUNICATIONS EQUIPMENT AND INSTALLATION SERVICES FOR THE
FIRE AND POLICE DEPARTMENTS, IN AN ESTIMATED AMOUNT OF $3,200,000.</p>

</main>
</body></html>
"""

SAMPLE_HTML_PROCUREMENT = """
<html><head><title>Current Bid Opportunities - City of Miami Beach Procurement</title></head>
<body>
<main>
<h1>Current Bid Opportunities</h1>
<p>Effective January 1, 2025, the City of Miami Beach Procurement Division has migrated
to Bidnet Direct for all solicitations.</p>

<table>
<tr><th>Solicitation #</th><th>Title</th><th>Due Date</th><th>Status</th></tr>
<tr><td>ITB 2026-098-AZ</td><td>Stormwater Pump Station Maintenance Services — Citywide</td>
<td>February 28, 2026</td><td>Open</td></tr>
<tr><td>RFQ 2026-105-BT</td><td>Professional Engineering Services for Drainage Master Plan Update</td>
<td>March 15, 2026</td><td>Open</td></tr>
<tr><td>ITB 2026-112-CK</td><td>Sanitary Sewer CCTV Inspection and Cleaning Services</td>
<td>February 20, 2026</td><td>Open</td></tr>
<tr><td>RFP 2026-088-DL</td><td>IT Managed Services and Cybersecurity</td>
<td>January 31, 2026</td><td>Closed</td></tr>
</table>
</main>
</body></html>
"""

SAMPLE_HTML_CIP = """
<html><head><title>Capital Improvement Program - City of Miami Beach</title></head>
<body>
<main>
<h1>Capital Improvement Program (CIP)</h1>
<p>The City of Miami Beach capital program includes water, sewer, and stormwater
infrastructure investments. The current 5-year CIP includes approximately 50 active
projects in planning, design, and construction phases.</p>

<h2>Stormwater Projects</h2>
<p><strong>SW-2025-008: Sunset Islands Drainage Improvements</strong><br>
Status: Under Construction | Contractor: Ric-Man International<br>
Contract Amount: $12,400,000 | Completion: December 2026<br>
This project addresses tidal flooding on Sunset Islands 1-4 through installation of
new pump stations, backflow preventers, and raised seawalls.</p>

<p><strong>SW-2026-003: West Avenue Stormwater Improvements Phase III</strong><br>
Status: Design 90% Complete | Consultant: Kimley-Horn<br>
Estimated Construction Cost: $18,500,000<br>
Scope includes new stormwater mains, pump station upgrades, and green infrastructure
improvements along West Avenue from 5th to 17th Streets. Construction authorization
expected Q2 FY 2026.</p>

<p><strong>SW-2026-014: Pump Station Improvements Phase II</strong><br>
Status: Design Authorization Pending | Consultant Selection: AECOM<br>
Estimated Design Fee: $2,450,000 | Estimated Construction: $15,000,000<br>
Design of improvements to seven stormwater pump stations in North Beach and Mid-Beach.</p>

<h2>Water Projects</h2>
<p><strong>WM-2025-011: 41st Street Water Main Replacement</strong><br>
Status: Active Construction | Contractor: Lanzo Construction<br>
Contract Amount: $4,125,000 | Completion: June 2026</p>

<h2>Sewer Projects</h2>
<p><strong>SS-2026-001: Force Main Replacement — Alton Road</strong><br>
Status: Permitting | Consultant: Hazen and Sawyer<br>
Estimated Cost: $9,800,000<br>
Replacement of aging 20-inch force main along Alton Road from 5th to Lincoln Road.
Environmental permit pending from FDEP. Timeline dependent on permit approval.</p>

</main>
</body></html>
"""


# ══════════════════════════════════════════════════════════════════════════════
#  TEST FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def test_relevance_scoring():
    log.info("── Test: Relevance scoring ──")
    sectors_cfg = load_yaml("config/sectors.yaml")
    kw_map = load_sector_keywords(sectors_cfg)

    samples = [
        ("Stormwater pump station improvements Phase II design authorization", "stormwater"),
        ("Water main replacement 41st Street corridor change order", "water"),
        ("Sanitary sewer rehabilitation program ITB specifications", "sewer"),
        ("Emergency communications equipment fire police departments", None),
        ("IT managed services cybersecurity procurement", None),
    ]

    for text, expected_sector in samples:
        scores = {s: score_relevance(text, s, kw_map) for s in ["stormwater", "water", "sewer"]}
        best = max(scores, key=scores.get) if max(scores.values()) > 0 else None
        status = "✅" if best == expected_sector else "❌"
        log.info("  %s  '%s...'  →  best=%s  scores=%s",
                 status, text[:50], best, scores)


def test_rule_classifiers():
    log.info("── Test: Rule-based classifiers ──")

    cases = [
        {
            "text": "AUTHORIZING THE CITY MANAGER TO EXECUTE A PROFESSIONAL SERVICES AGREEMENT FOR DESIGN SERVICES",
            "expect_stage": "Design Authorization",
            "expect_type": "Commission Agenda",
        },
        {
            "text": "ITB 2026-098-AZ Stormwater Pump Station Maintenance Services — Citywide. Due February 28, 2026. Open.",
            "expect_stage": "Prequalification (ITB/RFQ set up)",
            "expect_type": "Procurement (ITB/RFQ)",
        },
        {
            "text": "APPROVE THE APPROPRIATION OF $8,500,000 FROM STORMWATER UTILITY FUND RESERVES. ITB 2026-113-MP anticipated Q3 FY 2026.",
            "expect_stage": "Funding Allocated",
            "expect_type": "Funding Allocation",
        },
        {
            "text": "DISCUSSION ITEM — Presentation on status of FY 2026 Sanitary Sewer Rehabilitation Program. Staff requesting direction to prepare ITB.",
            "expect_stage": "Concept/Discussion",
            "expect_type": "Commission Agenda",
        },
        {
            "text": "Status: Active Construction. Contractor: Lanzo. Contract Amount: $4,125,000. Change Order No. 2.",
            "expect_stage": "Active Contract",
            "expect_type": "",
        },
    ]

    for case in cases:
        stage, _ = classify_procurement_stage(case["text"])
        sig_type = classify_signal_type(case["text"], "meetings")

        s1 = "✅" if stage == case["expect_stage"] else "❌"
        s2 = "✅" if sig_type == case["expect_type"] else "❌"
        log.info("  %s Stage: got='%s' want='%s'", s1, stage, case["expect_stage"])
        log.info("  %s Type:  got='%s' want='%s'", s2, sig_type, case["expect_type"])


def test_amount_extraction():
    log.info("── Test: Amount extraction ──")

    text = """The contract amount is $2,450,000.00 for design services.
    The estimated construction cost is $15,000,000. The total program is $45 million."""

    amounts = extract_amounts(text)
    log.info("  Found %d amounts:", len(amounts))
    for a in amounts:
        log.info("    %s  (parsed: %s)  context: %s", a["raw"], a["value"], a["context"][:60])

    best = select_best_amount(amounts)
    log.info("  Best amount selected: %s", best)


def test_html_parsing():
    log.info("── Test: HTML parsing — agenda page ──")
    chunks = parse_html(SAMPLE_HTML_AGENDA,
                        "https://www.miamibeachfl.gov/commission-agenda/2026-01-15/",
                        category="meetings")
    log.info("  Extracted %d chunks from agenda", len(chunks))
    for c in chunks:
        log.info("    [%s] %s", c.chunk_type, c.title[:80])

    log.info("── Test: HTML parsing — procurement page ──")
    chunks2 = parse_html(SAMPLE_HTML_PROCUREMENT,
                         "https://www.miamibeachfl.gov/procurement/bid-opportunities/",
                         category="procurement")
    log.info("  Extracted %d chunks from procurement", len(chunks2))
    for c in chunks2:
        log.info("    [%s] %s", c.chunk_type, c.title[:80])

    log.info("── Test: HTML parsing — CIP page ──")
    chunks3 = parse_html(SAMPLE_HTML_CIP,
                         "https://www.miamibeachfl.gov/cip/",
                         category="cip")
    log.info("  Extracted %d chunks from CIP", len(chunks3))
    for c in chunks3:
        log.info("    [%s] %s", c.chunk_type, c.title[:80])

    return chunks + chunks2 + chunks3


def test_full_pipeline_offline(all_chunks):
    """Run full classify + export on parsed chunks (no crawling, no LLM)."""
    log.info("── Test: Full offline pipeline ──")

    sectors_cfg = load_yaml("config/sectors.yaml")
    kw_map = load_sector_keywords(sectors_cfg)
    selected_sectors = ["stormwater", "water", "sewer"]
    default_agency = "City of Miami Beach"
    default_geo = "Miami Beach, FL"

    signals = []

    for chunk in all_chunks:
        ok, sector, rel_score = is_relevant(
            chunk.text, selected_sectors, kw_map, threshold=0.05
        )
        if not ok:
            continue

        text = chunk.text
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
        strength = infer_signal_strength(text, proc_stage, bool(best_amount), bool(meeting_date))
        fit = infer_strategic_fit(rel_score, proc_stage, strength)

        sig = Signal(
            signal_title=chunk.title[:120] or f"{agency} — {sector} signal",
            agency=agency,
            geography=geography,
            sector=sector,
            estimated_value=best_amount,
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
            strategic_notes=f"Detected {sector} signal at {proc_stage or 'unknown'} stage.",
            source_link=chunk.source_url,
            source_page_url=chunk.source_url,
            evidence_snippet=(proc_evidence or text[:200])[:500],
            evidence_page=chunk.page_number,
            confidence_score=round(rel_score, 3),
            extraction_method="rule",
            raw_amounts=amounts,
            relevance_score=rel_score,
        )
        signals.append(sig)

    log.info("  Relevant signals: %d", len(signals))
    for s in signals:
        log.info("    [%s] %s  |  stage=%s  strength=%s  fit=%s  value=%s",
                 s.sector, s.signal_title[:60], s.procurement_stage,
                 s.signal_strength, s.strategic_fit, s.estimated_value)

    # Export
    os.makedirs("data/output", exist_ok=True)
    xlsx_path = export_excel(signals, "data/output/test_signals.xlsx", include_audit=True)
    csv_path = export_csv(signals, "data/output/test_signals.csv")
    log.info("  Excel: %s", xlsx_path)
    log.info("  CSV:   %s", csv_path)

    return signals


# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    test_relevance_scoring()
    print()
    test_rule_classifiers()
    print()
    test_amount_extraction()
    print()
    chunks = test_html_parsing()
    print()
    signals = test_full_pipeline_offline(chunks)
    print()
    print(f"🏁  All tests complete — {len(signals)} signals exported")
