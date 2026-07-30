# LakeStream — Tech Detection & Fingerprinting

A pipeline that takes a list of company websites and identifies the technology
stack each one runs — CMS, frameworks, JS libraries, analytics, CDN, hosting,
web server, OS, programming language — plus email hosting provider (via MX
records) and SSL certificate issuer/expiry.

## How it works

For every domain in your input file, the pipeline:

1. **Fetches** the homepage over HTTP(S) (with `www.`/non-`www` and
   `http`/`https` fallbacks).
2. **Parses** the HTML/headers with a curated, evidence-based detector
   (`src/scraping/parser/tech_parser.py`). Every match records *how* it was
   found — `<meta name="generator">`, an HTTP header, a `<script src>` URL, an
   inline script global, or a plain HTML substring — and is scored
   `high` / `medium` / `low` confidence accordingly. Detections rated
   `high`/`medium` are flagged `recommended`.
3. **Cross-checks** with [Wappalyzer](https://github.com/HTTPArchive/wappalyzer)'s
   ~3,999-fingerprint database (`src/scraping/parser/wappalyzer_runner.py`) run
   in a process pool, to catch anything the curated rules miss.
4. **Enriches** the domain with its email hosting provider (MX record lookup)
   and SSL certificate issuer/expiry (TLS handshake).
5. **Merges** curated + Wappalyzer results into one row per site and writes
   the result to CSV.

Curated detections and Wappalyzer detections are never treated as identical —
curated hits carry their confidence label (e.g. `Django (low)`), Wappalyzer-only
hits are appended plain, so you can always tell which layer found what.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Requires Python 3.12+.

## Running it

Use `run_tech_detection.py` at the project root — the simple entry point for
one-off tech-stack runs:

```bash
python run_tech_detection.py path/to/companies.csv
python run_tech_detection.py path/to/companies.xlsx
```

Or just run it with no arguments and it will prompt you for a file path:

```bash
python run_tech_detection.py
Enter the path to the CSV or Excel file with companies/websites: companies.csv
```

**Input format:** a CSV or `.xlsx` with a header row containing at least a
website/domain column (`website`, `domain`, `url`, or `web_address`). A
company-name column (`name`, `company`, `party_name`, ...) and a `country`
column are picked up automatically if present, but aren't required.

**Output:** written next to the input file as `<input_name>_tech_stack.csv`,
with one row per site: detected platform, frameworks, JS libraries,
analytics, CDN, hosting, web server, OS, programming languages, widgets,
email hosting, SSL issuer/expiry, and a full detection list with confidence
levels.

### Larger, resumable runs

For very large batches (50k+ rows) that need checkpointing and can resume
after an interruption, see the scripts in `scripts/` — e.g.
`scripts/run_tech_detection_128k.py` — which add a SQLite-backed cache and
periodic CSV checkpointing on top of the same detection pipeline.

## Notes

- No CSV, Excel, or log files from prior runs are tracked in this repo (see
  `.gitignore`) — inputs and outputs are expected to stay local since they can
  contain customer/prospect data.
- Network calls (page fetch, MX lookup, SSL handshake) are best-effort and
  never raise — failures are recorded per-row as `FETCH_ERROR` or blank
  enrichment fields rather than aborting the run.
