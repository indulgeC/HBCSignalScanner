"""
Signal Scanner — Streamlit UI

Run with:
    streamlit run app.py
"""

import os
import sys
import logging
import datetime

import streamlit as st
import pandas as pd
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import SignalPipeline, load_yaml
from models.signal import Signal

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="HBC Signal Scanner",
    page_icon="🔍",
    layout="wide",
)

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

# ── Helpers ───────────────────────────────────────────────────────────────────

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")
SITES_DIR = os.path.join(CONFIG_DIR, "sites")
SECTORS_PATH = os.path.join(CONFIG_DIR, "sectors.yaml")


def get_available_sites() -> list[str]:
    if not os.path.isdir(SITES_DIR):
        return []
    return sorted(f.replace(".yaml", "") for f in os.listdir(SITES_DIR) if f.endswith(".yaml"))


def get_available_sectors() -> list[str]:
    cfg = load_yaml(SECTORS_PATH)
    return list(cfg.get("sectors", {}).keys())


def load_site_config(site_name: str) -> dict:
    path = os.path.join(SITES_DIR, f"{site_name}.yaml")
    return load_yaml(path)


def save_site_config(site_name: str, cfg: dict):
    path = os.path.join(SITES_DIR, f"{site_name}.yaml")
    os.makedirs(SITES_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def load_sectors_config() -> dict:
    return load_yaml(SECTORS_PATH)


def save_sectors_config(cfg: dict):
    with open(SECTORS_PATH, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def signals_to_df(signals: list[Signal]) -> pd.DataFrame:
    rows = [s.to_display_dict() for s in signals]
    return pd.DataFrame(rows)


# ── Emoji mappings for visual distinction ──────────────────────────────────

EMOJI_MAP = {
    "signal_strength": {"High": "🟢", "Medium": "🟡", "Low": "🔴"},
    "strategic_fit": {"Strong Fit": "🟢", "Moderate Fit": "🟡", "Monitor": "🔵", "No Fit": "🔴"},
    "friction_level": {"Low": "🟢", "Moderate": "🟡", "High": "🔴"},
}

EMOJI_COLUMNS = list(EMOJI_MAP.keys())


def _add_emoji(col: str, val: str) -> str:
    """Prepend emoji to a value for display."""
    emoji = EMOJI_MAP.get(col, {}).get(val, "")
    return f"{emoji} {val}" if emoji and val else val


def _strip_emoji(val: str) -> str:
    """Remove leading emoji + space from a display value."""
    if isinstance(val, str) and len(val) >= 2 and val[1] == " ":
        return val[2:].strip()
    # Handle multi-byte emoji (flag, colored circles, etc.)
    import re
    return re.sub(r"^[\U0001f300-\U0001fad6\u26aa\u26ab\U0001f534\U0001f535\U0001f7e0-\U0001f7eb]+\s*", "", val).strip() if isinstance(val, str) else val


def add_emojis_to_df(df: pd.DataFrame) -> pd.DataFrame:
    """Add emoji prefixes to indicator columns for display."""
    df = df.copy()
    for col in EMOJI_COLUMNS:
        if col in df.columns:
            df[col] = df[col].apply(lambda v, c=col: _add_emoji(c, v))
    return df


def strip_emojis_from_df(df: pd.DataFrame) -> pd.DataFrame:
    """Remove emoji prefixes from indicator columns before saving."""
    df = df.copy()
    for col in EMOJI_COLUMNS:
        if col in df.columns:
            df[col] = df[col].apply(_strip_emoji)
    return df


# ── Dropdown options for editable columns ────────────────────────────────────

def _emoji_options(col: str, values: list[str]) -> list[str]:
    """Build dropdown options with emoji prefixes."""
    return [_add_emoji(col, v) for v in values]


DROPDOWN_OPTIONS = {
    "procurement_stage": [
        "Concept/Discussion",
        "Committee Referral",
        "Funding Allocated",
        "Prequalification (ITB/RFQ)",
        "Design Authorization",
        "Construction Authorization",
        "Active Contract",
    ],
    "expected_timeline": [
        "0-3 months",
        "3-6 months",
        "6-12 months",
        "12+ months",
    ],
    "signal_strength": _emoji_options("signal_strength", ["High", "Medium", "Low"]),
    "strategic_fit": _emoji_options("strategic_fit", ["Strong Fit", "Moderate Fit", "Monitor", "No Fit"]),
    "signal_type": [
        "Commission Agenda",
        "Capital Budget",
        "Procurement (ITB/RFQ)",
        "Policy Direction",
        "Funding Allocation",
    ],
    "lifecycle_stage": [
        "Concept/Policy Direction",
        "Budget Inclusion",
        "Funding Confirmed",
        "Design Advancement",
        "Procurement Imminent",
    ],
    "friction_level": _emoji_options("friction_level", ["Low", "Moderate", "High"]),
    "momentum": ["Accelerating", "Stable", "Stalled", "Unclear"],
}


def rebuild_signals_from_df(df: pd.DataFrame, original_signals: list[Signal]) -> list[Signal]:
    """Apply edits from the DataFrame back onto Signal objects.

    The df may be a filtered subset, so we use its original integer index
    to look up the corresponding Signal in the full list.
    """
    clean_df = strip_emojis_from_df(df)
    updated = list(original_signals)  # shallow copy
    for idx, row in clean_df.iterrows():
        if idx < len(original_signals):
            base = original_signals[idx].to_full_dict()
            edits = {col: row[col] for col in Signal.DISPLAY_COLUMNS if col in clean_df.columns}
            updated[idx] = Signal(**{**base, **edits})
    return updated


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🔍 HBC Signal Scanner")
    st.caption("Government infrastructure signal intelligence")

    st.divider()

    # Site selection
    sites = get_available_sites()
    selected_sites = st.multiselect(
        "Sites",
        options=sites,
        default=sites[:1] if sites else [],
        help="Select one or more government sites to scan",
    )

    # Sector selection
    sectors = get_available_sectors()
    selected_sectors = st.multiselect(
        "Sectors",
        options=sectors,
        default=["stormwater"] if "stormwater" in sectors else sectors[:1],
        help="Select sectors to filter for",
    )

    # Year filter
    current_year = datetime.datetime.now().year
    year_mode = st.radio(
        "Year filter",
        options=["Upcoming", "Specific years", "Year range"],
        horizontal=True,
        help="Upcoming = future meetings; Specific years = pick multiple; Range = from-to.",
    )

    selected_years: list[int] = []
    if year_mode == "Specific years":
        selected_years = st.multiselect(
            "Years",
            options=[y for y in range(current_year, current_year - 11, -1)],
            default=[current_year],
        )
    elif year_mode == "Year range":
        rcol1, rcol2 = st.columns(2)
        start_year = rcol1.number_input(
            "From",
            min_value=current_year - 20,
            max_value=current_year,
            value=current_year - 2,
            step=1,
        )
        end_year = rcol2.number_input(
            "To",
            min_value=current_year - 20,
            max_value=current_year,
            value=current_year,
            step=1,
        )
        if start_year <= end_year:
            selected_years = list(range(int(start_year), int(end_year) + 1))
        else:
            st.error("Start year must be ≤ end year")

    st.divider()

    # Advanced options
    with st.expander("Advanced Options"):
        max_pages = st.number_input(
            "Max pages per site",
            min_value=10, max_value=500, value=100, step=10,
        )
        threshold = st.slider(
            "Relevance threshold",
            min_value=0.01, max_value=0.5, value=0.05, step=0.01,
        )
        use_llm = st.checkbox("Enable AI enrichment", value=False)

        if use_llm:
            llm_api_key = st.text_input(
                "Anthropic API Key",
                value=os.environ.get("ANTHROPIC_API_KEY", ""),
                type="password",
                help="Leave blank to use ANTHROPIC_API_KEY env var",
            )
            llm_model = st.selectbox(
                "AI Model",
                options=[
                    "claude-sonnet-4-20250514",
                    "claude-haiku-4-5-20251001",
                    "claude-opus-4-20250514",
                ],
                index=0,
                help="Model used for signal enrichment",
            )
            if not llm_api_key:
                st.warning("Enter an API key or set `ANTHROPIC_API_KEY` env var")
        else:
            llm_api_key = ""
            llm_model = ""

    st.divider()
    run_btn = st.button("🚀 Run Scan", type="primary", use_container_width=True)


# ── Main area — Tabs ─────────────────────────────────────────────────────────

tab_scan, tab_sites, tab_sectors = st.tabs(["📊 Scan Results", "🌐 Site Config", "🏗️ Sector Config"])

# ══════════════════════════════════════════════════════════════════════════════
#  TAB 1: Scan Results
# ══════════════════════════════════════════════════════════════════════════════

with tab_scan:
    st.header("Infrastructure Signal Intelligence")

    if not selected_sites or not selected_sectors:
        st.info("Select at least one **site** and one **sector** in the sidebar, then click **Run Scan**.")

    # Warn when AI enrichment is on but no API key available
    _env_key_present = bool(os.environ.get("ANTHROPIC_API_KEY", ""))
    if use_llm and not llm_api_key and not _env_key_present:
        st.info(
            "💡 **AI enrichment is enabled but no API key detected.** "
            "Either paste your Anthropic API key in the sidebar (Advanced Options) "
            "or uncheck AI enrichment to run in rule-only mode. "
            "Get a key at https://console.anthropic.com"
        )

    # ── Run pipeline ─────────────────────────────────────────────────
    if run_btn and selected_sites and selected_sectors:
        with st.status("Scanning...", expanded=True) as status:
            st.write(f"**Sites:** {', '.join(selected_sites)}")
            st.write(f"**Sectors:** {', '.join(selected_sectors)}")
            if selected_years:
                st.write(f"**Years:** {', '.join(str(y) for y in selected_years)}")
            else:
                st.write("**Year:** Upcoming meetings")

            # Progress bar + live status message
            progress_bar = st.progress(0.0, text="Initializing...")
            status_text = st.empty()

            def update_progress(pct: float, msg: str):
                try:
                    safe_pct = max(0.0, min(1.0, float(pct)))
                    progress_bar.progress(safe_pct, text=msg)
                    status_text.info(msg)
                except Exception:
                    pass

            output_dir = os.path.join("data", "output")
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, "signals.xlsx")

            pipeline = SignalPipeline(
                site_names=selected_sites,
                sectors=selected_sectors,
                config_dir=CONFIG_DIR,
                use_llm=use_llm,
                relevance_threshold=threshold,
                max_pages=max_pages,
                years=selected_years or None,
                llm_api_key=llm_api_key if use_llm else "",
                llm_model=llm_model if use_llm else "",
                progress_callback=update_progress,
            )

            try:
                result_path = pipeline.run(output_path=output_path)
                st.session_state["signals"] = pipeline.signals
                st.session_state["output_path"] = result_path
                progress_bar.progress(1.0, text=f"Complete — {len(pipeline.signals)} signals")
                status_text.success(f"✅ Found {len(pipeline.signals)} signals")
                status.update(label=f"✅ Complete — {len(pipeline.signals)} signals", state="complete")
            except Exception as e:
                status_text.error(f"❌ {e}")
                status.update(label=f"❌ Error: {e}", state="error")
                st.error(str(e))

    # ── Display results ──────────────────────────────────────────────
    if "signals" in st.session_state and st.session_state["signals"]:
        signals = st.session_state["signals"]
        df = signals_to_df(signals)

        # Summary metrics
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Signals", len(signals))
        col2.metric("High Strength", len([s for s in signals if s.signal_strength == "High"]))
        col3.metric("Strong Fit", len([s for s in signals if s.strategic_fit == "Strong Fit"]))
        col4.metric("Sectors", len(set(s.sector for s in signals)))

        st.divider()

        # Filters
        fcol1, fcol2, fcol3 = st.columns(3)
        with fcol1:
            strength_filter = st.multiselect(
                "Filter by Strength",
                ["High", "Medium", "Low"],
                default=["High", "Medium", "Low"],
            )
        with fcol2:
            fit_filter = st.multiselect(
                "Filter by Fit",
                ["Strong Fit", "Moderate Fit", "Monitor", "No Fit"],
                default=["Strong Fit", "Moderate Fit", "Monitor"],
            )
        with fcol3:
            sector_filter = st.multiselect(
                "Filter by Sector",
                sorted(set(s.sector for s in signals)),
                default=sorted(set(s.sector for s in signals)),
            )

        # Apply filters (on raw values before emoji)
        mask = (
            df["signal_strength"].isin(strength_filter)
            & df["strategic_fit"].isin(fit_filter)
            & df["sector"].isin(sector_filter)
        )
        filtered = add_emojis_to_df(df[mask])

        st.subheader(f"Signals ({len(filtered)} shown)")
        st.caption("All cells are editable. Dropdown columns have predefined options; text columns can be typed freely.")

        # Build column_config for st.data_editor
        editor_column_config = {
            # ── Dropdown columns ─────────────────────────────────
            "procurement_stage": st.column_config.SelectboxColumn(
                "Procurement Stage",
                options=DROPDOWN_OPTIONS["procurement_stage"],
                width="medium",
            ),
            "expected_timeline": st.column_config.SelectboxColumn(
                "Expected Timeline",
                options=DROPDOWN_OPTIONS["expected_timeline"],
                width="small",
            ),
            "signal_strength": st.column_config.SelectboxColumn(
                "Signal Strength",
                options=DROPDOWN_OPTIONS["signal_strength"],
                width="small",
            ),
            "strategic_fit": st.column_config.SelectboxColumn(
                "Strategic Fit",
                options=DROPDOWN_OPTIONS["strategic_fit"],
                width="small",
            ),
            "signal_type": st.column_config.SelectboxColumn(
                "Signal Type",
                options=DROPDOWN_OPTIONS["signal_type"],
                width="medium",
            ),
            "lifecycle_stage": st.column_config.SelectboxColumn(
                "Lifecycle Stage",
                options=DROPDOWN_OPTIONS["lifecycle_stage"],
                width="medium",
            ),
            "friction_level": st.column_config.SelectboxColumn(
                "Friction Level",
                options=DROPDOWN_OPTIONS["friction_level"],
                width="small",
            ),
            "momentum": st.column_config.SelectboxColumn(
                "Momentum",
                options=DROPDOWN_OPTIONS["momentum"],
                width="small",
            ),
            # ── Free-text columns ────────────────────────────────
            "signal_title": st.column_config.TextColumn("Signal Title", width="large"),
            "agency": st.column_config.TextColumn("Agency", width="medium"),
            "geography": st.column_config.TextColumn("Geography", width="medium"),
            "sector": st.column_config.TextColumn("Sector", width="small"),
            "estimated_value": st.column_config.TextColumn("Est. Value", width="small"),
            "meeting_date": st.column_config.TextColumn("Meeting Date", width="small"),
            "trigger_event": st.column_config.TextColumn("Trigger Event", width="large"),
            "strategic_notes": st.column_config.TextColumn("Strategic Notes", width="large"),
            "source_link": st.column_config.LinkColumn("Source Link", width="medium"),
        }

        edited_df = st.data_editor(
            filtered,
            use_container_width=True,
            height=500,
            column_config=editor_column_config,
            num_rows="fixed",
            key="signal_editor",
        )

        # ── Auto-save: sync edits back and re-export ────────
        if edited_df is not None:
            from exporters.excel import export_excel, export_csv

            updated_signals = rebuild_signals_from_df(edited_df, signals)
            st.session_state["signals"] = updated_signals

            output_dir = os.path.join("data", "output")
            os.makedirs(output_dir, exist_ok=True)
            out_path = os.path.join(output_dir, "signals.xlsx")
            export_excel(updated_signals, out_path, include_audit=True)
            csv_out = out_path.rsplit(".", 1)[0] + ".csv"
            export_csv(updated_signals, csv_out)
            st.session_state["output_path"] = out_path

        # ── Download buttons ─────────────────────────────────
        st.divider()

        dcol1, dcol2 = st.columns(2)

        output_path = st.session_state.get("output_path", "")
        if output_path and os.path.exists(output_path):
            with open(output_path, "rb") as f:
                dcol1.download_button(
                    "📥 Download Excel",
                    data=f.read(),
                    file_name="signals.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.document",
                )

        csv_path = output_path.rsplit(".", 1)[0] + ".csv" if output_path else ""
        if csv_path and os.path.exists(csv_path):
            with open(csv_path, "rb") as f:
                dcol2.download_button(
                    "📥 Download CSV",
                    data=f.read(),
                    file_name="signals.csv",
                    mime="text/csv",
                )

    elif "signals" in st.session_state:
        st.warning("No signals found matching your criteria. Try broadening your sectors or lowering the relevance threshold.")


# ══════════════════════════════════════════════════════════════════════════════
#  TAB 2: Site Configuration
# ══════════════════════════════════════════════════════════════════════════════

with tab_sites:
    st.header("Site Configuration")
    st.caption("Add or edit government website configurations")

    site_tab_new, site_tab_edit = st.tabs(["➕ Add New Site", "✏️ Edit Existing Site"])

    # ── Add new site ─────────────────────────────────────────────────
    with site_tab_new:
        with st.form("new_site_form"):
            st.subheader("New Site")

            new_name = st.text_input("Site ID (e.g. miami_beach)", placeholder="city_name")
            new_display = st.text_input("Display Name", placeholder="City of Miami Beach")
            new_base_url = st.text_input("Base URL", placeholder="https://www.example.gov")
            new_agency = st.text_input("Default Agency", placeholder="City of Miami Beach")
            new_geo = st.text_input("Default Geography", placeholder="Miami Beach, FL")

            st.divider()
            st.markdown("**Crawler Mode**")
            new_crawler_mode = st.selectbox(
                "Crawler Mode",
                options=["primegov", "default"],
                help="'primegov' uses PrimeGov API for meeting agendas. 'default' uses BFS web crawler.",
                key="new_crawler_mode",
            )
            new_primegov_base = st.text_input(
                "PrimeGov API Base URL (if using primegov mode)",
                placeholder="https://yourcity.primegov.com",
                key="new_primegov_base",
            )

            st.divider()
            st.markdown("**Crawl Settings**")
            ncol1, ncol2, ncol3 = st.columns(3)
            new_max_depth = ncol1.number_input("Max Depth", min_value=1, max_value=10, value=3, key="new_depth")
            new_max_pages = ncol2.number_input("Max Pages", min_value=10, max_value=1000, value=200, key="new_pages")
            new_delay = ncol3.number_input("Request Delay (s)", min_value=0.5, max_value=10.0, value=1.0, step=0.5, key="new_delay")

            st.divider()
            new_seeds_text = st.text_area(
                "Seed URLs (one per line, format: url | category | label)",
                placeholder="https://www.example.gov/meetings/ | meetings | Meetings & Agendas\nhttps://www.example.gov/procurement/ | procurement | Bid Opportunities",
                height=120,
                key="new_seeds_text",
            )

            new_domains_text = st.text_input(
                "Allowed Domains (comma-separated)",
                placeholder="example.gov, www.example.gov",
                key="new_domains",
            )

            submitted_new = st.form_submit_button("💾 Save New Site", type="primary")

            if submitted_new and new_name:
                # Build config dict
                seeds = []
                for line in (new_seeds_text or "").strip().splitlines():
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 1 and parts[0]:
                        seed = {"url": parts[0]}
                        if len(parts) >= 2:
                            seed["category"] = parts[1]
                        if len(parts) >= 3:
                            seed["label"] = parts[2]
                        seeds.append(seed)

                domains = [d.strip() for d in (new_domains_text or "").split(",") if d.strip()]

                cfg = {
                    "name": new_name,
                    "display_name": new_display or new_name,
                    "base_url": new_base_url,
                    "default_agency": new_agency,
                    "default_geography": new_geo,
                    "crawler_mode": new_crawler_mode,
                    "allowed_domains": domains,
                    "seeds": seeds,
                    "max_depth": new_max_depth,
                    "max_pages": new_max_pages,
                    "request_delay_seconds": new_delay,
                }
                if new_crawler_mode == "primegov" and new_primegov_base:
                    cfg["primegov_api_base"] = new_primegov_base

                save_site_config(new_name, cfg)
                st.success(f"Site '{new_name}' saved!")
                st.rerun()

    # ── Edit existing site ───────────────────────────────────────────
    with site_tab_edit:
        existing_sites = get_available_sites()
        if not existing_sites:
            st.info("No sites configured yet. Add one in the 'Add New Site' tab.")
        else:
            edit_site = st.selectbox("Select site to edit", existing_sites, key="edit_site_select")

            if edit_site:
                cfg = load_site_config(edit_site)

                with st.form("edit_site_form"):
                    st.subheader(f"Edit: {cfg.get('display_name', edit_site)}")

                    e_display = st.text_input("Display Name", value=cfg.get("display_name", ""), key="e_display")
                    e_base_url = st.text_input("Base URL", value=cfg.get("base_url", ""), key="e_base_url")
                    e_agency = st.text_input("Default Agency", value=cfg.get("default_agency", ""), key="e_agency")
                    e_geo = st.text_input("Default Geography", value=cfg.get("default_geography", ""), key="e_geo")

                    st.divider()
                    st.markdown("**Crawler Mode**")
                    e_crawler_mode = st.selectbox(
                        "Crawler Mode",
                        options=["primegov", "default"],
                        index=0 if cfg.get("crawler_mode") == "primegov" else 1,
                        key="e_crawler_mode",
                    )
                    e_primegov_base = st.text_input(
                        "PrimeGov API Base URL",
                        value=cfg.get("primegov_api_base", ""),
                        key="e_primegov_base",
                    )

                    st.divider()
                    st.markdown("**Crawl Settings**")
                    ec1, ec2, ec3 = st.columns(3)
                    e_max_depth = ec1.number_input("Max Depth", min_value=1, max_value=10, value=cfg.get("max_depth", 3), key="e_depth")
                    e_max_pages = ec2.number_input("Max Pages", min_value=10, max_value=1000, value=cfg.get("max_pages", 200), key="e_pages")
                    e_delay = ec3.number_input("Request Delay (s)", min_value=0.5, max_value=10.0, value=float(cfg.get("request_delay_seconds", 1.0)), step=0.5, key="e_delay")

                    st.divider()
                    # Seeds — format existing seeds for editing
                    existing_seeds_lines = []
                    for s in cfg.get("seeds", []):
                        if isinstance(s, dict):
                            parts = [s.get("url", ""), s.get("category", ""), s.get("label", "")]
                            existing_seeds_lines.append(" | ".join(parts))
                        else:
                            existing_seeds_lines.append(str(s))

                    e_seeds_text = st.text_area(
                        "Seed URLs (one per line, format: url | category | label)",
                        value="\n".join(existing_seeds_lines),
                        height=150,
                        key="e_seeds_text",
                    )

                    e_domains_text = st.text_input(
                        "Allowed Domains (comma-separated)",
                        value=", ".join(cfg.get("allowed_domains", [])),
                        key="e_domains",
                    )

                    st.divider()
                    e_priority_text = st.text_area(
                        "Priority URL Patterns (one per line)",
                        value="\n".join(cfg.get("priority_patterns", [])),
                        height=100,
                        key="e_priority",
                    )
                    e_ignore_text = st.text_area(
                        "Ignore URL Patterns (one per line)",
                        value="\n".join(cfg.get("ignore_patterns", [])),
                        height=100,
                        key="e_ignore",
                    )

                    submitted_edit = st.form_submit_button("💾 Save Changes", type="primary")

                    if submitted_edit:
                        seeds = []
                        for line in (e_seeds_text or "").strip().splitlines():
                            parts = [p.strip() for p in line.split("|")]
                            if len(parts) >= 1 and parts[0]:
                                seed = {"url": parts[0]}
                                if len(parts) >= 2:
                                    seed["category"] = parts[1]
                                if len(parts) >= 3:
                                    seed["label"] = parts[2]
                                seeds.append(seed)

                        domains = [d.strip() for d in (e_domains_text or "").split(",") if d.strip()]
                        priority = [p.strip() for p in (e_priority_text or "").splitlines() if p.strip()]
                        ignore = [p.strip() for p in (e_ignore_text or "").splitlines() if p.strip()]

                        updated = {
                            "name": edit_site,
                            "display_name": e_display,
                            "base_url": e_base_url,
                            "default_agency": e_agency,
                            "default_geography": e_geo,
                            "crawler_mode": e_crawler_mode,
                            "allowed_domains": domains,
                            "seeds": seeds,
                            "priority_patterns": priority,
                            "ignore_patterns": ignore,
                            "max_depth": e_max_depth,
                            "max_pages": e_max_pages,
                            "request_delay_seconds": e_delay,
                        }
                        if e_crawler_mode == "primegov" and e_primegov_base:
                            updated["primegov_api_base"] = e_primegov_base

                        save_site_config(edit_site, updated)
                        st.success(f"Site '{edit_site}' updated!")
                        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
#  TAB 3: Sector Configuration
# ══════════════════════════════════════════════════════════════════════════════

with tab_sectors:
    st.header("Sector Configuration")
    st.caption("Define keyword lists for each infrastructure sector")

    sectors_cfg = load_sectors_config()
    all_sectors = sectors_cfg.get("sectors", {})

    # ── Add new sector ───────────────────────────────────────────────
    with st.expander("➕ Add New Sector"):
        with st.form("new_sector_form"):
            new_sector_name = st.text_input("Sector Name (e.g. transportation)", key="new_sector_name")
            new_sector_keywords = st.text_area(
                "Keywords (one per line)",
                placeholder="highway\nroad repair\nbridge\ntraffic signal",
                height=150,
                key="new_sector_kw",
            )
            add_sector_btn = st.form_submit_button("💾 Add Sector", type="primary")

            if add_sector_btn and new_sector_name:
                kw_list = [k.strip() for k in new_sector_keywords.splitlines() if k.strip()]
                if kw_list:
                    all_sectors[new_sector_name.lower().strip()] = {"keywords": kw_list}
                    sectors_cfg["sectors"] = all_sectors
                    save_sectors_config(sectors_cfg)
                    st.success(f"Sector '{new_sector_name}' added with {len(kw_list)} keywords!")
                    st.rerun()
                else:
                    st.warning("Please enter at least one keyword.")

    st.divider()

    # ── Edit existing sectors ────────────────────────────────────────
    if not all_sectors:
        st.info("No sectors configured yet.")
    else:
        for sector_name, sector_data in all_sectors.items():
            with st.expander(f"🏗️ {sector_name.title()} ({len(sector_data.get('keywords', []))} keywords)"):
                with st.form(f"edit_sector_{sector_name}"):
                    keywords_text = st.text_area(
                        "Keywords (one per line)",
                        value="\n".join(sector_data.get("keywords", [])),
                        height=200,
                        key=f"kw_{sector_name}",
                    )

                    sc1, sc2 = st.columns(2)
                    save_btn = sc1.form_submit_button("💾 Save")
                    delete_btn = sc2.form_submit_button("🗑️ Delete Sector")

                    if save_btn:
                        kw_list = [k.strip() for k in keywords_text.splitlines() if k.strip()]
                        all_sectors[sector_name]["keywords"] = kw_list
                        sectors_cfg["sectors"] = all_sectors
                        save_sectors_config(sectors_cfg)
                        st.success(f"Sector '{sector_name}' updated with {len(kw_list)} keywords!")
                        st.rerun()

                    if delete_btn:
                        del all_sectors[sector_name]
                        sectors_cfg["sectors"] = all_sectors
                        save_sectors_config(sectors_cfg)
                        st.success(f"Sector '{sector_name}' deleted.")
                        st.rerun()
