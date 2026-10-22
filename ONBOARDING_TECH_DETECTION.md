# Onboarding: How LakeStream Works (for Beginners)

Welcome! This doc explains this codebase — nicknamed **LakeStream** — from scratch. It assumes no prior knowledge of the project, and only light familiarity with web development in general. Jargon is defined the first time it's used.

If you just want the "official" quick-start commands, see [README.md](README.md). This doc instead focuses on **understanding how the pieces fit together**, with a deep dive into one feature — technology-stack detection — as a worked example of "how this codebase actually thinks."

---

## 1. What is this app, in one paragraph

LakeStream is a **B2B web-scraping and data-extraction platform**, built by Lake B2B. In plain terms: you give it a list of company websites, and it visits each one, reads the page, and pulls out useful facts — blog posts, contact info, pricing, and (the feature we'll dig into) **what software/technology a site is built with** (WordPress? React? Shopify? Google Analytics?). Think of it like a search-engine crawler, except instead of just indexing pages for search, it *reads each page closely* and turns what it finds into structured data (spreadsheet rows, database records) for two kinds of teams: **SEO teams** (competitor monitoring, site audits) and **data/sales teams** (lead enrichment — figuring out what a company uses so sales can pitch relevantly).

## 2. The big picture — how a request flows through the system

```
                 ┌─────────────┐        ┌──────────────┐
  You / a tool → │   FastAPI    │  jobs  │  arq + Redis │
  (browser, CLI, │   API server │ ─────► │  job queue   │
   or another    │ src/server.py│        └──────┬───────┘
   app)          └──────┬───────┘               │
                         │ reads/writes          ▼
                         ▼                ┌──────────────┐
                  ┌─────────────┐         │   Workers    │
                  │ PostgreSQL  │ ◄────── │ (background  │
                  │  database   │  saves  │  processes)  │
                  └─────────────┘  results└──────┬───────┘
                                                  │ for each page it fetches:
                                                  ▼
                                        ┌───────────────────────┐
                                        │  Scraping engine       │
                                        │  (3 tiers, see below)  │
                                        │  → TechParser runs here │
                                        └───────────────────────┘
```

- **API server** (`src/server.py`) — a [FastAPI](https://fastapi.tiangolo.com/) app (FastAPI is a Python framework for building web APIs; it runs on an ASGI server called Uvicorn). This is the front door: it receives requests like "scrape this URL" or "search for companies" and hands them off as **jobs**.
- **Job queue** (`src/queue/`, using a library called `arq`, backed by **Redis**, an in-memory data store) — scraping a website can take a few seconds, so instead of making you wait on the API call, the server drops the job in a queue and immediately replies "I'm on it, here's a job ID."
- **Workers** (`src/workers/`) — separate background processes that pick jobs off the queue, do the actual slow work (fetching pages, parsing them), and save results.
- **PostgreSQL** — the database where scraped results, job status, and company profiles are stored.
- **CLI** (`src/cli/app.py`, installed as the `lakestream` command) — a command-line alternative to using the API directly, for people who prefer a terminal.
- There's also an **MCP server** (`src/mcp_server.py`) that exposes these same capabilities as tools an AI agent (like Claude) can call, and a **Chrome extension** (`extension/`) for manually scraping LinkedIn/Apollo pages from a browser.

### The "three-tier" scraping strategy

Fetching a page isn't one-size-fits-all — some sites block bots. LakeStream tries progressively heavier tools:

1. **Tier 1 — Basic HTTP** (fast, cheap): a plain HTTP request, like `curl`. Works for most sites.
2. **Tier 2 — Headless Browser** (Playwright): if the page needs JavaScript to render its content (common with modern frameworks), it opens a real (invisible) browser to load the page fully.
3. **Tier 3 — Stealth + Proxy**: if the site is actively blocking bots (Cloudflare challenges, CAPTCHAs), it escalates to using proxies and stealth techniques.

It auto-escalates: only pays the cost of a heavier tier when a lighter one fails.

## 3. Running it locally (short version)

Full details live in [README.md](README.md) and [V2_LOCAL_TESTING.md](V2_LOCAL_TESTING.md) — this is just enough to get oriented. All commands below are `Makefile` targets, run from the repo root:

| Command | What it does |
|---|---|
| `pip install -r requirements.txt` | Installs Python dependencies |
| `make docker-up` | Starts Postgres + Redis in Docker containers |
| `make dev` | Starts the API server (`uvicorn src.server:app --reload`) on port 8000 |
| `make worker` | Starts the background worker process (`arq src.queue.worker.WorkerSettings`) |
| `make test` | Runs the test suite (`pytest tests/ -v`) |

You need `dev` **and** `worker` both running for scrape jobs to actually complete — the API just queues jobs, the worker does the work. Configuration (database URL, Redis URL, API keys) lives in a `.env` file — copy `.env.example` to `.env` and fill in values to get started.

## 4. Deep dive: how technology-stack detection actually works

This is the feature this repo has been actively refining (see recent commit history), so it's a good worked example of the codebase's style: fast, deterministic pattern-matching first, with an AI model as a fallback only when needed.

### The core idea: it's a detective, not a browser

The component that does this is `TechParser`, in **`src/scraping/parser/tech_parser.py`**. Crucially: **`TechParser` makes no network requests itself.** By the time it runs, some other part of the system (a worker, or a standalone script) has already fetched the page and handed `TechParser` two things:

- the raw **HTML** text of the page
- the **HTTP response headers** (metadata the server sent back, like `Server: nginx` or `X-Powered-By: Express`)

```python
tp = TechParser(html, headers)
result = tp.detect()
```

This is the same basic approach used by the well-known browser extension [Wappalyzer](https://www.wappalyzer.com/) — look for known fingerprints in the page source, rather than actually running the site's code.

### The "signatures" — a database of known fingerprints

**`src/data/tech_signatures.py`** contains a Python list, `TECH_SIGNATURES`, with ~184 entries — one per technology LakeStream knows how to recognize, across ~20 categories (`cms`, `analytics`, `framework`, `cdn`, `hosting`, `js_library`, `payment`, `database`, etc.). Two real examples from the file:

```python
{
    "name": "WordPress",
    "category": "cms",
    "signals": ["wp-content", "wp-includes", "wp-json", "/wp-admin"],
    "meta_generator": "wordpress",
},
{"name": "Cloudflare", "category": "cdn", "signals": [], "header_signals": ["cf-ray", "server: cloudflare"]},
```

Each entry can define:
- `signals` — substrings to look for in the page's script/link URLs, inline scripts, or raw HTML.
- `header_signals` (optional) — substrings to look for *only* in HTTP headers.
- `meta_generator` (optional) — an exact-ish match against `<meta name="generator" content="...">`, a tag many CMS platforms add automatically.

### The 5-layer detection pipeline

For every signature, `_match_with_evidence()` checks five "layers," in order from most trustworthy to least, and **stops at the first one that matches**:

| Layer | Where it looks | Confidence assigned |
|---|---|---|
| 1. `meta_generator` | `<meta name="generator">` tag | **high** |
| 2. `header` | HTTP response headers | **high** |
| 3. `script_url` / `link_url` | `<script src="...">` / `<link href="...">` URLs | **medium** |
| 4. `inline_script` | Text inside `<script>...</script>` blocks | **medium** |
| 5. `html_fallback` | Anywhere in the raw HTML | **low** |

Why layered like this? A `<meta name="generator" content="WordPress">` tag is a near-certain signal — the site is *declaring* what it's built with. But finding the word "wordpress" loosely somewhere in the page HTML is much weaker evidence — maybe it's just a blog post mentioning WordPress. So the parser trusts strong signals first and only falls back to weak substring matching if nothing better was found.

Every match is recorded as a rich result, not just a name:

```python
{
    "name": "WordPress",
    "category": "cms",
    "confidence": "high",              # from the layer that matched
    "evidence": "wordpress",           # the actual string that matched
    "evidence_type": "meta_generator", # which layer found it
    "recommended": True,               # True for high/medium, False for low
}
```

That `evidence` field matters a lot for trust and debugging: instead of just saying "we think this site uses WordPress," the system can show *why* — "because we found `<meta name='generator' content='wordpress'>`." This is what the codebase's commit history calls "evidence tracking," and `recommended` is a convenience flag so anything consuming these results can easily filter out the noisy, low-confidence guesses.

### A real bug this design caught: false positives from linked (not hosted) CDNs

Here's a concrete lesson baked into the current signature file. Early versions of the Cloudflare/AWS/Azure signatures included **URL substrings** like `cdnjs.cloudflare.com` or `cloudfront.net` in their `signals` list. The problem: a site can *link to* a JavaScript library hosted on Cloudflare's public CDN (a common, totally unrelated practice) without the site itself being *hosted behind* Cloudflare. That caused the parser to wrongly flag almost any site using a popular CDN-hosted library as "using Cloudflare" or "hosted on AWS."

The fix: these entries were changed to `"signals": []` (no URL matching at all) and made **header-only**, because headers like `cf-ray` (Cloudflare) or `x-amz-cf-id` (AWS CloudFront) are only ever sent by a server that is *actually* fronted by that specific provider — a page merely referencing a CDN asset URL never causes those headers to appear:

```python
{"name": "Cloudflare", "category": "cdn", "signals": [], "header_signals": ["cf-ray", "server: cloudflare"]},
{"name": "AWS CloudFront", "category": "cdn", "signals": [], "header_signals": ["x-amz-cf-id", "x-amz-cf-pop"]},
{"name": "AWS", "category": "hosting", "signals": [], "header_signals": ["x-amz-request-id", "x-amz-id-2", "server: aws"]},
```

In a measured test batch of 84 sites, this dropped false positives from 23 down to 6, and raised high-confidence accuracy from 89.8% to 99.0%. The general lesson: **"this page references technology X" is not the same claim as "this page is built with/hosted on technology X"** — and the confidence-layer design exists specifically to keep those two claims separate.

### The AI fallback — used sparingly, only when needed

Regex/substring matching can't catch everything (a site built with an obscure or custom stack might have no known fingerprint at all). For those cases, **`src/services/llm_extractor.py`** sends the page's HTML to an AI model (via a service called OpenRouter) and asks it to guess the tech stack from the same kinds of signals a human would look for. This only runs when `TechParser` found **zero** `recommended` (high/medium confidence) detections — it's a fallback safety net, not the primary method, because it's slower and costs money per page, whereas the signature matcher is instant and free.

### Where this plugs into the rest of the app

- **Live crawling**: when a normal scrape job asks for `tech_stack` data, `src/workers/content_worker.py` calls `TechParser` on every fetched page (not just the homepage) and saves the result as a `TechStackMetadata` record (defined in `src/models/scraped_data.py`) in the database.
- **Batch testing**: `scripts/run_tech_detection_500.py` is a standalone script (not part of the live API) that runs the exact same detector across a big spreadsheet of companies at once — see the next section.

## 5. Try it yourself

### Run the existing tests

```bash
pytest tests/unit/scraping/test_tech_parser.py -v
```

This file has ~12 tests that each assert: "given this sample HTML, `TechParser` should report this technology with this confidence and this evidence type." Reading these tests is a great way to see the detector's expected behavior in miniature, without reading the full implementation.

### Run the 500-company batch script

The repo includes `companies_500.csv` / `companies_500.xlsx` — a sample input list of 500 companies with columns `Company_Name, WEBSITE, COUNTRY`. To scan all of them:

```bash
python scripts/run_tech_detection_500.py
```

What happens:
1. It reads `companies_500.xlsx`.
2. It fetches every company's website concurrently (up to 50 at a time, using the `httpx` library), with a spoofed browser `User-Agent` and a 30-second timeout per site.
3. For each successfully fetched page, it runs `TechParser` (the exact logic from section 4).
4. Any company where the parser found **no** recommended detections gets sent to the AI fallback instead.
5. Progress is checkpointed as it goes — if you stop and rerun the script, it skips companies already processed.
6. It writes `companies_500_tech_stacks.xlsx` and `.csv`, with one row per company. Key columns: `status` (`OK` / `FETCH_ERROR` / `EMPTY`), `detections` (e.g. `"WordPress (high); React (medium)"`), `high_confidence` / `medium_confidence` / `low_confidence` (comma-separated tech names per tier), and `llm_*` columns holding the AI fallback's guesses where it was used.

A companion script, `scripts/validate_tech_stack.py`, independently double-checks the detector's output against hand-written ground-truth rules (e.g., "WordPress is confirmed if `wp-content` or `wp-includes` literally appears in the HTML") — this is how accuracy numbers mentioned in the project's commit history (like "88.4% overall accuracy") were measured.

## 6. Where things live — a quick file map

| Path | What it is |
|---|---|
| `src/server.py` | The FastAPI app — the API's entry point |
| `src/api/routes/` | Individual API endpoints (scrape, discover, enrich, export, etc.) |
| `src/cli/app.py` | The `lakestream` command-line tool |
| `src/queue/worker.py` | Background job-queue worker entry point |
| `src/workers/content_worker.py` | Processes a single scraped page: runs parsers (including `TechParser`), saves results |
| `src/scraping/fetcher/` | The three-tier fetching logic (HTTP / Playwright / stealth+proxy) |
| `src/scraping/parser/tech_parser.py` | The technology-detection engine described in section 4 |
| `src/data/tech_signatures.py` | The ~184 technology fingerprints `TechParser` matches against |
| `src/services/llm_extractor.py` | The AI-based fallback extractor |
| `src/services/enrichment.py` | Firmographic "enrichment" logic (see glossary below) |
| `scripts/run_tech_detection_500.py` | Batch-runs tech detection over a spreadsheet of companies |
| `tests/unit/scraping/test_tech_parser.py` | Unit tests documenting expected detector behavior |
| `Makefile` | Shortcut commands (`make dev`, `make worker`, `make test`, ...) |

## 7. Glossary — terms you'll run into elsewhere in this codebase

- **Enrichment** — taking a bare company name/domain and filling in extra facts about it: industry classification (mapped to standard codes like NAICS/SIC), logo, social links, tech stack, job postings. Implemented in `src/services/enrichment.py`; exposed via `POST /api/enrich`. A separate, external orchestration project calls LakeStream's API repeatedly to build a full enrichment pipeline — LakeStream itself is the "engine" that does the actual fetching/extracting.
- **Confidence** — how sure the tech-stack detector is about a match: `high` / `medium` / `low`, based on which detection layer found it (section 4).
- **Evidence** — the literal string or snippet that caused a detection to fire, kept alongside every result so matches are explainable, not just asserted.
- **LLM fallback** — using an AI language model to guess an answer only when deterministic pattern-matching comes up empty; used for tech-stack detection and other extraction tasks.
- **Dev tunnel** — `scripts/dev-link.sh` (or `docker compose -f docker-compose.local.yml --profile tunnel up`) exposes your local `localhost:7100` server via a temporary public URL (using `localtunnel`, `cloudflared`, or `ngrok`), so you can open your local instance from another device, e.g. to demo something or test on a phone.
- **Tiered scraping** — see section 2; escalating from a plain HTTP request up to a full stealth browser only as needed, to keep the common case fast and cheap.

## 8. Where to go next

This doc intentionally doesn't duplicate everything — for more detail, see:

- [README.md](README.md) — full quick-start, API endpoint list, use-case examples
- [docs/API.md](docs/API.md) — API reference
- [docs/SOP.md](docs/SOP.md) — how to use the web dashboard UI day-to-day (aimed at non-engineers)
- [V2_LOCAL_TESTING.md](V2_LOCAL_TESTING.md) — full local dev setup, feature flags, auth modes, the dev tunnel
- [INTERN_HANDOFF_100_TEST.md](INTERN_HANDOFF_100_TEST.md) — a deeper walkthrough of the enrichment pipeline and how it connects to an external orchestrator project

> Note: this doc describes the code as of the time it was written. If something here seems to disagree with what you see in the source, trust the source — code changes faster than docs.
