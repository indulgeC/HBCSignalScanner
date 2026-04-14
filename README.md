# Signal Scanner

A configurable pipeline that scans government websites for infrastructure procurement signals, classifies them by sector, and exports structured data to Excel.

## Architecture

```
config/          Site & sector YAML configs
crawler/         Breadth-first web crawler, file downloader
parsers/         HTML page parser, PDF text extractor, chunk splitter
classifiers/     Relevance scorer, rule-based field extraction, LLM enrichment
models/          Signal dataclass (one row in the output grid)
exporters/       Excel (.xlsx) and CSV writers
pipeline.py      Orchestrator: crawl → parse → filter → classify → export
cli.py           Command-line interface
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your Anthropic API key (optional — works without it in rule-only mode)
export ANTHROPIC_API_KEY=sk-ant-...

# 3. Run the scanner
python cli.py --site miami_beach --sector stormwater

# 4. Find your results
#    data/output/signals.xlsx
#    data/output/signals.csv
```

## CLI Options

```
python cli.py \
  --site miami_beach          # Site config name (repeatable)
  --sector stormwater         # Sector to scan (repeatable)
  --sector water              # Add more sectors
  --output results/scan.xlsx  # Custom output path
  --max-pages 50              # Limit crawl size
  --threshold 0.1             # Relevance cutoff (0.0–1.0)
  --no-llm                    # Rule-only mode, no API calls
  --verbose                   # Debug logging
```

## Adding a New Site

Create `config/sites/your_site.yaml`:

```yaml
name: your_site
display_name: "City of Example"
base_url: "https://www.example.gov"
default_agency: "City of Example"
default_geography: "Example, FL"

allowed_domains:
  - "example.gov"

seeds:
  - url: "https://www.example.gov/meetings/"
    category: meetings
    label: "Meetings"
  - url: "https://www.example.gov/procurement/"
    category: procurement
    label: "Procurement"

priority_patterns:
  - "/meetings/"
  - "/procurement/"

ignore_patterns:
  - "/careers/"
  - ".jpg"

max_depth: 3
max_pages: 200
request_delay_seconds: 1.5
```

Then run:
```bash
python cli.py --site your_site --sector stormwater
```

## Adding a New Sector

Edit `config/sectors.yaml`:

```yaml
sectors:
  your_sector:
    keywords:
      - keyword1
      - keyword2
      - "multi word phrase"
```

## Output Fields

| Field | Method | Notes |
|---|---|---|
| Signal Title | AI | Templated: [Agency] + [Action] + [Asset] |
| Agency | Rule | From text → site default |
| Geography | Rule | From text → site default |
| Sector | Input | Your selected sector |
| Estimated Value | Rule + AI | Rule finds amounts, AI picks best |
| Expected Timeline | Rule | Stage default, text override |
| Meeting Date | Rule | Priority: title → page → meta |
| Signal Type | Rule | Keyword mapping |
| Procurement Stage | Rule | Keyword mapping, most specific wins |
| Lifecycle Stage | Derived | From Procurement Stage + Signal Type |
| Signal Strength | Rule + AI | Scoring system |
| Strategic Fit | Rule + AI | Relevance × strength × stage |
| Friction Level | Rule | Friction keyword scan |
| Momentum | Future | Requires project history (Phase 3) |
| Trigger Event | Rule | Short phrase extraction |
| Strategic Notes | AI | 2-sentence template |

## Phases

- **Phase 1** ✅ Miami Beach + stormwater, end-to-end pipeline
- **Phase 2** — Multi-sector, multi-site, Excel styling, audit fields
- **Phase 3** — Cross-document project merging, momentum tracking, dedup improvements
