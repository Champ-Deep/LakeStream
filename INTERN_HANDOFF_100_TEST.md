# Intern Hand-off — 100-Company Enrichment Test (LakeStream, local)

> **Mission (one line):** take the first **100** of the 1,000-company list and
> produce **one combined file** where every company has: verified **domain**,
> its **careers/jobs source**, the **actual job postings** (structured), and a
> **tech-stack** read — all run locally on your machine, multi-threaded, with an
> OpenRouter LLM layer used only where deterministic scraping falls short.
>
> This is the same test ladder Deep set (100 → 500 → 1,000 → full). Do **not**
> scale past 100 until the Gate-1 bar below is signed off.

---

## 0. The honest scope (read first — it saves you a week)

Three of the four asks are very scrapable. One is not, and one source is off-limits:

| Ask | Reality | Where it comes from |
|---|---|---|
| Company **domain** | Easy, 90%+ | resolve + **verify against the homepage's own name** (not search rank) |
| **Careers/jobs source** | Moderate | ATS boards first (the real "career portal"), then careers page |
| **Job details** | Moderate | ATS JSON APIs → JSON-LD `JobPosting` → LLM over rendered page |
| **Tech stack (digital)** | Easy-moderate, 85% | LakeStream `tech_stack` extraction (CDN/cloud/CMS/martech) |
| **Hardware brand** (Lenovo taxonomy) | **Hard, ~⅓ from scrape** | job-post keyword tagging + case studies; the rest needs a vendor |

**Two guardrails set by Deep's own Build Plan + Intern Playbook — keep them:**

1. **No LinkedIn scraping at scale.** It's excluded on Cadence and here for ToS
   reasons. You asked about LinkedIn Jobs / Google Jobs / job portals — the
   *legitimately scrapable* "job portals" are the **ATS boards** (Greenhouse,
   Lever, Ashby, Workable, SmartRecruiters, Recruitee, BambooHR), and those are
   the strongest signal anyway. Google Jobs is bot-hostile; treat it as a
   supplementary, rate-limited last resort, not a primary. LinkedIn stays a
   manual spot-check only. (See §6 for exactly how to add extra sources safely.)
2. **Evidence or it didn't happen.** Every tag/field carries a source URL + a
   snippet. No inferred value without a quote. This is also the Lenovo
   procurement governance story, so it's not optional.

Per-field fill-rate targets for the 100 (this is what "consistently pull all of
them" means honestly — *not* 100% on every field):

| Field | Target on 100 |
|---|---|
| Domain resolved & verified | ≥ 90% |
| Careers/ATS source found | 55–70% |
| ≥ 1 structured job posting | 45–60% |
| Digital tech stack detected | 80–90% |
| Hardware keyword tag w/ evidence | 25–45% (expected — don't force it) |

---

## 1. Architecture — two parts, clear split

```
   companies_100.csv
          │
          ▼
  ┌───────────────────────────┐        HTTP (X-API-Key)      ┌──────────────────────────┐
  │  pipeline-v2 (orchestrator)│  ─────────────────────────▶ │  LakeStream (engine, LOCAL)│
  │  champ-enrich repo         │                             │  docker compose :7100     │
  │  • domain resolve + verify │  /api/search  (domain fallback)   • 3-tier + Go fetchers │
  │  • careers/ATS ladder      │  /api/scrape/url (render → md)    • JS rendering         │
  │  • job extraction ladder   │  /api/enrich  (firmographics)     • LLM extractor        │
  │  • keyword tagging         │  /api/scrape/execute (tech_stack) • Postgres cache       │
  │  • multi-thread + LLM layer│  /api/parse   (PDF/doc → md)      • change monitoring    │
  └───────────────────────────┘                             └──────────────────────────┘
          │
          ▼
   results.csv + jobs.csv + tech.csv  (one combined file, §7)
```

- **LakeStream** is the *engine*: it fetches (with JS rendering + block
  escalation + the fast Go HTTP tier), converts to clean Markdown, extracts
  structured data, and now enriches firmographics. You run it locally.
- **pipeline-v2** (the `pipeline-v2/` zip / `champ-enrich` repo you already have)
  is the *orchestrator*: the per-company loop, the ATS ladder, the keyword
  tagging, the resumable state, the scoreboard, and the multi-pass LLM rule.

You do **not** rebuild one inside the other. You point the orchestrator at the
engine over HTTP (patch in §3).

---

## 2. Local setup (on your machine)

### 2a. Start LakeStream (the engine)

```bash
git clone https://github.com/Champ-Deep/LakeStream.git
cd LakeStream
git checkout claude/v2-scraper-comparison-qykhm6      # the v2 + v2.1 test branch

# one command brings up postgres/redis/api/worker + Go fetchers, migrations auto-run
docker compose -f docker-compose.local.yml up --build
# → http://localhost:7100
```

Set your OpenRouter key so LakeStream's own extractor + enrichment can use the
LLM (edit `docker-compose.local.yml` under the `api` and `worker` services, then
re-up):

```yaml
      OPENROUTER_API_KEY: "sk-or-..."
      LLM_EXTRACTION_MODEL: "google/gemini-2.5-flash-lite"   # configurable string
```

Get an API key (used as `X-API-Key`): log into http://localhost:7100
(`admin@lakeb2b.internal` / `LakeB2B_admin!`), **Settings → API keys**, create
one. It looks like `ls_...`. Verify:

```bash
export LAKESTREAM_URL="http://localhost:7100"
export LAKESTREAM_API_KEY="ls_..."
curl -s $LAKESTREAM_URL/api/health
# {"status":"ok","database":"connected","redis":"connected",...}
```

### 2b. Set up pipeline-v2 (the orchestrator)

```bash
git clone git@github.com:Champ-Deep/champ-enrich.git   # or unzip pipeline-v2.zip
cd champ-enrich
pip install -r requirements.txt
python smoke_test.py            # must print all-passing, offline

export OPENROUTER_API_KEY="sk-or-..."
export LAKESTREAM_URL="http://localhost:7100"
export LAKESTREAM_API_KEY="ls_..."
```

Drop the **real first 100 rows** of the client list into `companies_100.csv`
(`name,domain,country`; extra columns ignored). The zip shipped placeholder rows.

---

## 3. Wire pipeline-v2 → LakeStream over HTTP (apply this patch)

As shipped, `fetch.py` only uses LakeStream when pipeline-v2 lives *inside* the
LakeStream repo (in-process import); otherwise it falls back to plain `requests`
(no JS rendering). Run them as separate repos and add an **HTTP path** so you get
LakeStream's JS rendering + block escalation locally.

Add to the top of `fetch.py`:

```python
import os
LAKESTREAM_URL = os.getenv("LAKESTREAM_URL", "")
LAKESTREAM_KEY = os.getenv("LAKESTREAM_API_KEY", "")

def _lakestream_http(url, tier=None):
    """Render a page via LakeStream's hosted API → (status, markdown)."""
    import requests
    body = {"url": url}
    if tier:
        body["tier"] = tier          # e.g. "go_http" (fast) or "playwright" (JS)
    r = requests.post(f"{LAKESTREAM_URL}/api/scrape/url",
                      headers={"X-API-Key": LAKESTREAM_KEY,
                               "Content-Type": "application/json"},
                      json=body, timeout=60)
    r.raise_for_status()
    d = r.json()
    return (d.get("status_code") or (200 if d.get("success") else 0)), (d.get("markdown") or "")
```

Then in `get(...)`, prefer the HTTP path when configured:

```python
    if render:
        if LAKESTREAM_URL and LAKESTREAM_KEY:
            try:
                status, body = _lakestream_http(url)      # JS-rendered Markdown
            except Exception:
                status, body = 0, ""
        else:
            try:                                          # in-repo fallback (unchanged)
                import asyncio
                res = asyncio.run(_lakestream().scrape(url))
                body = res.get("markdown") or res.get("content") or ""
                status = 200 if body else 0
            except Exception:
                pass
```

Now every `render=True` fetch (careers pages, case studies) goes through
LakeStream: JS widgets load, blocks escalate to the browser/proxy tiers, and you
get clean Markdown back.

> **Careers-page fetches should use `render=True`** — many careers pages are
> JS-only and come back empty with plain `requests`.

---

## 4. Domain resolution (verified, not search-rank)

pipeline-v2's `domains.py` already does the right thing: guess → search →
**fetch the homepage and require it to claim the company's own name** (title /
og:site_name / copyright), with a multi-country legal-suffix strip and an
aggregator blocklist. Keep that. Two upgrades:

- **Swap the DuckDuckGo fallback for LakeStream `/api/search`** (LakeCurrent).
  This is the pre-agreed pivot for "search source keeps getting blocked" — no
  captcha wall, and no paid Serper/Brave key:

  ```bash
  curl -s $LAKESTREAM_URL/api/search -H "X-API-Key: $LAKESTREAM_API_KEY" \
    -H 'Content-Type: application/json' \
    -d '{"query":"\"Société Générale de Mécanique\" official website","limit":5}'
  ```

  In `domains.py::search_candidates`, when DuckDuckGo returns nothing, call
  `/api/search` and read `results[].domain` in rank order.

- **Or skip resolution entirely per company** with LakeStream `/api/enrich`,
  which resolves domain **and** returns firmographics in one call (§6b).

---

## 5. Careers/ATS discovery + job extraction

Keep pipeline-v2's ladder (`careers.py` → `jobs.py`) — it's the correct,
cheapest-first design:

1. **ATS detection** (`careers.py::detect_ats`) — if the homepage/careers page
   references Greenhouse/Lever/Ashby/Workable/Recruitee/SmartRecruiters/BambooHR,
   you get a **free structured JSON feed** of every posting. This is the biggest
   single win and the real "career portal."
2. **Careers link scan** (multilingual) → **sitemap.xml** → common paths.
   Fetch the careers page with `render=True` (→ LakeStream) so JS boards load.
3. **Job extraction ladder** (`jobs.py::extract`): ATS JSON → JSON-LD
   `JobPosting` → heuristics → **LLM only when a page exists but nothing
   structured came out**. Every job carries title, location, department, url,
   posted_date, description, and an **IT/procurement flag** (that's where the
   hardware evidence lives).

Verify one company end to end before scaling:

```bash
# render a careers page through LakeStream
curl -s $LAKESTREAM_URL/api/scrape/url -H "X-API-Key: $LAKESTREAM_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.example.com/careers","tier":"playwright"}' | head -c 400
```

---

## 6. The broader job sourcing you asked about (do this *safely*)

You wanted LinkedIn Jobs / Google Jobs / job portals on top of the careers page.
Here's the honest, compliant version:

### 6a. Extra sources — allowed, and how

- **ATS boards = the job portals.** Already covered in §5. This is where the
  structured, ban-proof data is. Don't skip it in favor of flashier sources.
- **Find more job URLs via `/api/search`** instead of scraping Google Jobs
  directly. Query `"{company} careers"` or `"{company} jobs"` and pick up ATS /
  aggregator links, then run them through the §5 ladder:

  ```bash
  curl -s $LAKESTREAM_URL/api/search -H "X-API-Key: $LAKESTREAM_API_KEY" \
    -H 'Content-Type: application/json' \
    -d '{"query":"Acme Corp careers greenhouse OR lever OR workday","limit":8}'
  ```
- **Google for Jobs**: technically scrapable but bot-hostile and JS-heavy — if
  you try it, go through LakeStream's `playwright` tier, keep volume tiny, and
  expect blocks. Supplementary only. Do **not** build the 100-run on it.

### 6b. **LinkedIn — do not automate at scale**

Your own Build Plan (§9) and the Intern Playbook (rule 3) exclude LinkedIn
scraping at scale for ToS reasons, same as Cadence. Keep it out of the pipeline.
If you need LinkedIn job signal: (a) a **manual** spot-check on a handful of
priority accounts, or (b) a **licensed** provider/API later — not a crawler.
Flag this to Deep before doing anything automated with LinkedIn.

### 6c. Tech stack — two layers, don't conflate them

- **Digital stack** (CDN, cloud host, CMS, analytics, martech): run a LakeStream
  crawl asking for the `tech_stack` data type. This is high-coverage and useful
  context, but it is **not** the hardware answer.

  ```bash
  curl -s $LAKESTREAM_URL/api/scrape/execute -H "X-API-Key: $LAKESTREAM_API_KEY" \
    -H 'Content-Type: application/json' \
    -d '{"domain":"example.com","max_pages":4,
         "data_types":["tech_stack","contact"],"llm_mode":"fallback","priority":5}'
  # → {"job_id":"..."}; poll:
  curl -s $LAKESTREAM_URL/api/jobs/<job_id> -H "X-API-Key: $LAKESTREAM_API_KEY"
  ```
- **Hardware brand** (the Lenovo taxonomy): comes from **keyword-tagging the job
  descriptions + case studies** (`lenovo_match.py`), not from a fingerprint. IT
  reqs name the gear ("administer Dell PowerEdge and NetApp"). Expect evidence on
  ~⅓ of companies — that's the known limit, not a bug.

### 6d. Company firmographics in one call — `/api/enrich`

New in this branch: give it a domain (or email, or name) and it returns
industry, **NAICS/SIC**, employee/revenue range, logo, description, socials —
cached per company. Good for the firmographic passthrough columns.

```bash
curl -s $LAKESTREAM_URL/api/enrich -H "X-API-Key: $LAKESTREAM_API_KEY" \
  -H 'Content-Type: application/json' -d '{"domain":"stripe.com"}'
```

---

## 7. Multi-threading (the speed you asked for)

Two independent layers of concurrency — use both:

- **LakeStream side (already concurrent):** the arq worker runs jobs in parallel
  and each fetch escalates tiers on its own. The **`go_http` tier** is the fast
  path — use it for plain page/careers fetches, reserve `playwright` for JS-only
  pages. Nothing to do here except prefer `go_http` where you can.
- **Orchestrator side (make it concurrent):** pipeline-v2's `run_pipeline.py`
  loops companies **sequentially** as shipped. Parallelize the per-company work
  with a thread pool (the work is I/O-bound, so threads are fine):

  ```python
  from concurrent.futures import ThreadPoolExecutor, as_completed
  import threading
  _write_lock = threading.Lock()

  def process_one(c):
      name = (c.get("name") or c.get("company") or "").strip()
      dom  = (c.get("domain") or c.get("website") or "").strip()
      country = (c.get("country") or "").strip()
      return name, process(name, dom, country, DICT)

  with ThreadPoolExecutor(max_workers=args.workers) as ex:   # add --workers, start at 8
      futures = [ex.submit(process_one, c) for c in todo]
      for fut in as_completed(futures):
          name, (row, cj, cn) = fut.result()
          with _write_lock:                                   # sqlite is single-writer
              state.execute("INSERT OR REPLACE INTO done VALUES (?,?,?,?)",
                            (name, json.dumps(row), json.dumps(cj), json.dumps(cn)))
              state.commit()
          rows.append(row); all_jobs += [...]; all_news += [...]
  ```

  Two gotchas when you thread this:
  - Open the sqlite caches with `sqlite3.connect(path, check_same_thread=False)`
    and guard every write with the lock above (or use one connection per thread).
  - Keep `POLITE_DELAY` per-domain — concurrency is *across* companies, never
    hammering one domain. LakeStream also rate-limits per domain, so you're
    doubly safe.

  Start at `--workers 8`, watch the domain-resolved rate hold, then raise. If it
  drops when you add workers, an IP got throttled — back off, don't push.

---

## 8. The LLM layer (OpenRouter — free first, then cheap)

**The rule that keeps cost near zero:** deterministic first (ATS JSON, JSON-LD,
regex, name verification), **LLM only on the residue** (ambiguous domains, career
pages with no structured jobs, risky short names, hardware disambiguation).
pipeline-v2 already enforces this; keep `LLM_BUDGET_USD=5` and **never raise it**
— hitting it means you're over-using the LLM, not that the cap is wrong.

**Model ladder — start free for the 100 test, move to cheap for the real run.**
Model is a configurable string (house rule), set via `LLM_MODEL`
(pipeline-v2) and `LLM_EXTRACTION_MODEL` (LakeStream):

| Stage | Use | Notes |
|---|---|---|
| Dev / the 100 test | a **free** OpenRouter model | 200 req/day free-tier cap is fine at 100 companies with the multi-pass rule. Also `LLM_PROVIDER=none` for a pure-deterministic baseline run. |
| Cheap production | **`google/gemini-2.5-flash-lite`** | the confirmed default in pipeline-v2's `config.py`; ~\$30 for the full 190K per Deep's estimate |
| Alternates | `google/gemini-2.5-flash`, `deepseek/deepseek-chat` | step up when Flash-Lite misses on hard extractions |

⚠️ **Verify exact model slugs at runtime — they change.** You named "DeepSeek V4
flash" and "Kimi 3.0"; I can't confirm those exact OpenRouter IDs from here, and
inventing a slug just makes calls 404. Before using any model, list what's
actually live and copy the exact `id`:

```bash
curl -s https://openrouter.ai/api/v1/models \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  | python3 -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data'] if any(k in m['id'] for k in ('gemini-2.5','deepseek','kimi','moonshot'))]"
```

Then set, e.g., `export LLM_MODEL="deepseek/deepseek-chat"` — no code change,
just the env var. Add the model's real `$/1M` price to `config.py::PRICES` so the
cost meter stays honest. If a newer DeepSeek/Kimi appears in that list and beats
Flash-Lite on your `error_log.md` cases, switch to it the same way.

---

## 9. Run the ladder — don't skip a gate

```bash
# 0) deterministic baseline (no key, zero cost) — proves the pipeline + schema
LLM_PROVIDER=none python run_pipeline.py --input companies_100.csv --outdir out_det --workers 8

# 1) with the LLM layer (free model first)
export LLM_PROVIDER=openrouter
export LLM_MODEL="<a free slug from §8>"
python run_pipeline.py --input companies_100.csv --outdir out_100 --workers 8
```

Re-runs are free and resumable (page cache + LLM cache + per-company state live
in the outdir) — kill it anytime, run again, it continues. Never delete the
outdir to "start clean" unless Deep says so.

**Gate-1 pass bar (sign-off before 500):**
- 100% of rows match the schema (§7 below), every column populated **or**
  blanked-with-reason;
- domain resolved & verified ≥ 90% (hand-check 20, sorted by lowest
  `domain_confidence` — check the *worst* rows);
- careers/ATS found where it exists (hand-check on all 100);
- tagging precision ≥ 85% on a spot check, **every tag carrying a source URL +
  snippet**;
- one page of measured numbers: careers-found rate, jobs-with-descriptions rate,
  hardware-tag fill rate, **pages/hour at 8 workers**, projected cost + wall-clock
  for the full run.

---

## 10. Output — one combined file (+ child tables)

**Parent, one row per company** (`results.csv`):
`company_id, name, domain, resolved_url, domain_confidence, crawl_status,`
firmographic passthrough (`industry, naics_code, sic_code, employee_range,
revenue_range` from `/api/enrich`), `digital_stack[],`
then per segment `{servers|pc_workstation|laptops|storage}_brand, _list_type,
_evidence, _source_url, _confidence,`
then job rollup `open_reqs, it_reqs, sample_titles, location_mix, dept_mix,
hiring_hardware_signals[],`
then `install_base_read, displacement_flag, confidence_tier, sources[], last_crawled`.

**Child, one row per posting** (`jobs.csv`): `company_id, job_title, department,
location, seniority, posted_date, employment_type, jd_text, hardware_mentions,
source_url`.

Deliver Parquet/CSV for the pipeline plus an Excel view for the team.

---

## 11. What to bring to the Monday check-in

- `out_100/` scoreboard + cost report + `review_queue.csv` count;
- the cleaned `keyword_dictionary.json`;
- one page of numbers (§9);
- top 5 error patterns from `error_log.md` (so we tune the dictionary + LLM rules
  together);
- pages/hour at 8 workers → projected time + cost for the full run.

**Non-negotiables:** every tag carries source URL + snippet · robots + polite
per-domain limits · no LinkedIn at scale · dedupe *after* ingest · secrets in
env, never in code · checkpoint everything (a big run must resume, never restart)
· LLM is the last resort, not the first.

---

*Engine: LakeStream `claude/v2-scraper-comparison-qykhm6`. Orchestrator:
pipeline-v2 / champ-enrich. Companion docs: `V2_LOCAL_TESTING.md`,
`Lenovo Intent Enrichment : Build Plan`, `Intern Playbook`.*
