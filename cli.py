#!/usr/bin/env python3
"""
Signal Scanner CLI — scan government sites for infrastructure signals.

Usage examples:
    # Basic: Miami Beach + stormwater
    python cli.py --site miami_beach --sector stormwater

    # Multiple sectors
    python cli.py --site miami_beach --sector stormwater --sector water --sector sewer

    # Limit crawl size & disable AI
    python cli.py --site miami_beach --sector stormwater --max-pages 50 --no-llm

    # Custom output
    python cli.py --site miami_beach --sector stormwater -o results/my_scan.xlsx
"""

import logging
import os
import sys

import click

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import SignalPipeline


def setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


@click.command()
@click.option(
    "--site", "-s",
    multiple=True,
    required=True,
    help="Site config name (e.g. miami_beach). Can repeat.",
)
@click.option(
    "--sector", "-S",
    multiple=True,
    required=True,
    help="Sector to scan for (e.g. stormwater). Can repeat.",
)
@click.option(
    "--output", "-o",
    default="data/output/signals.xlsx",
    show_default=True,
    help="Output Excel file path.",
)
@click.option(
    "--config-dir", "-c",
    default="config",
    show_default=True,
    help="Config directory.",
)
@click.option(
    "--max-pages",
    default=None,
    type=int,
    help="Override max pages to crawl per site.",
)
@click.option(
    "--threshold",
    default=0.05,
    show_default=True,
    type=float,
    help="Minimum relevance score to include a signal.",
)
@click.option(
    "--no-llm",
    is_flag=True,
    default=False,
    help="Disable LLM enrichment (rule-only mode).",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    default=False,
    help="Verbose logging.",
)
@click.option(
    "--no-merge",
    is_flag=True,
    default=False,
    help="Keep all signals (don't merge by project). Still computes momentum.",
)
@click.option(
    "--year", "-y",
    default=None,
    type=int,
    help="Filter meetings by year (e.g. 2025). Omit for upcoming meetings.",
)
def main(site, sector, output, config_dir, max_pages, threshold, no_llm, verbose, no_merge, year):
    """Scan government websites for infrastructure procurement signals."""
    setup_logging(verbose)

    sites = list(site)
    sectors = list(sector)

    click.echo(f"🔍  Signal Scanner")
    click.echo(f"    Sites:   {', '.join(sites)}")
    click.echo(f"    Sectors: {', '.join(sectors)}")
    click.echo(f"    Year:    {year or 'upcoming'}")
    click.echo(f"    LLM:     {'off' if no_llm else 'on'}")
    click.echo(f"    Merge:   {'off' if no_merge else 'on'}")
    click.echo(f"    Output:  {output}")
    click.echo()

    pipeline = SignalPipeline(
        site_names=sites,
        sectors=sectors,
        config_dir=config_dir,
        use_llm=not no_llm,
        relevance_threshold=threshold,
        max_pages=max_pages,
        merge_projects=not no_merge,
        year=year,
    )

    result_path = pipeline.run(output_path=output)

    click.echo()
    click.echo(f"✅  Done — {len(pipeline.signals)} signals ({len(pipeline.projects)} projects)")
    click.echo(f"    Excel: {result_path}")
    click.echo(f"    CSV:   {result_path.rsplit('.', 1)[0]}.csv")


if __name__ == "__main__":
    main()
