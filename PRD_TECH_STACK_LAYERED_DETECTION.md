# PRD: Tech Stack detection as a layered option (curated / Wappalyzer / LLM)

**Status:** Implemented and verified end-to-end against the live app (uncommitted on branch `tech_detect_wapp+fingerprinter`). Not yet committed, not yet exposed in CLI/MCP, not yet applied to the "Add Site" recurring-monitor modal.

## 1. Background

LakeStream's dashboard lets a user pick what to extract from a site via a set
of `data_types` checkboxes (Blog Posts, Articles, Contacts, **Tech Stack**,
Resources, Pricing) — `src/static/js/app.js:226`, rendered in
`src/templates/web/pages/dashboard.html:125`. "Tech Stack" was already one of
these, backed by `TechParser` (`src/scraping/parser/tech_parser.py`), called
from `src/workers/content_worker.py`.

Separately, isolated work on this branch had built two more detection layers
that were never wired into the live app:
- A full **Wappalyzer library** pass (3999 official fingerprints,
  `src/scraping/parser/wappalyzer_runner.py`), previously only used from the
  standalone batch CSV scripts (`scripts/run_tech_detection_*.py`).
- An **LLM fallback** (`src/services/llm_extractor.py`, `tech_stack` prompt),
  previously only reachable via the live app's *generic* `llm_mode` toggle,
  which applies uniformly to every data type — there was no tech-stack-
  specific fallback.

## 2. Goal

Expose Wappalyzer and the tech-stack LLM fallback as **independent, opt-in
add-ons** on top of the existing curated "Tech Stack" checkbox in the live
app — not always-on, not bundled — so a user can choose:
- just the fast curated detector (default, unchanged behavior),
- **+ Wappalyzer** for broader coverage (slower),
- **+ AI fallback** for a guess when nothing else matched (only when zero
  recommended detections exist — a safety net, not an always-run option).

**Scope:** live app only (FastAPI job pipeline + dashboard UI). The batch
scripts (`scripts/run_tech_detection_*.py`) and the CLI/MCP surfaces were
explicitly left untouched — see §6.

## 3. Decisions made

| Question | Decision |
|---|---|
| Should the curated detector's default signature set stop bundling the ~10k Wappalyzer-converted signatures already merged into `TechParser` (`tech_parser.py:83`)? | **No** — left as-is. "Wappalyzer add-on" = running the actual `python-Wappalyzer` library as an *extra* pass, not touching the curated merge. |
| When should the LLM fallback fire? | **Only when zero recommended (high/medium confidence) detections** exist from curated (+ Wappalyzer, if enabled) — minimizes cost, matches original design intent. |
| Batch scripts too, or live app only? | **Live app only.** Batch scripts keep their current always-on Wappalyzer blend. |
| Where do the new toggles live in the UI? | **Nested under the "Tech Stack" checkbox**, shown only when it's checked. |

## 4. What was implemented

Job-option plumbing follows the exact existing pattern used by `raw_only`/
`llm_mode` — no DB migration needed (these are per-job kwargs passed through
Redis, not persisted columns):

`ScrapeJobInput` → `src/api/routes/scrape.py` (enqueue kwargs) →
`process_scrape_job` (`src/queue/jobs.py`) → `ContentWorker(**kwargs)` →
`BaseWorker.__init__` (`self.attr`) → read in `content_worker.py`.

### Files changed

- **`src/models/job.py`** — `ScrapeJobInput` gained two fields, both default
  `False` (fully backward compatible):
  - `tech_stack_wappalyzer: bool`
  - `tech_stack_llm_fallback: bool`
- **`src/api/routes/scrape.py`** — passes both through to
  `redis.enqueue_job(...)`.
- **`src/queue/jobs.py`** — `process_scrape_job` accepts both, forwards to
  `ContentWorker(...)`.
- **`src/workers/base.py`** — `BaseWorker.__init__` accepts and stores
  `self.tech_stack_wappalyzer` / `self.tech_stack_llm_fallback`.
- **`src/workers/content_worker.py`** — the core logic:
  - `_extract_tech_stack` is now `async` and returns `list[dict]` (was
    `dict | None`). Call site (`records.extend(await self._extract_tech_stack(...))`)
    updated accordingly.
  - **Homepage-only gate** (`urlparse(url).path.rstrip("/") in ("", "/index.html")`)
    applies to both add-ons — they're the expensive layers; the curated pass
    still scans every page as before.
  - **Wappalyzer add-on**: `detect_wappalyzer_full()` run via
    `asyncio.to_thread` (CPU-bound regex work, don't block the event loop),
    merged into the curated result via a new static helper
    `ContentWorker._merge_wappalyzer_detections(detected, wapp_full)` —
    dedupes by name (case-insensitive) against what curated already found,
    buckets new names into the right flat-list field via the existing
    `wapp_by_column()` helper, and appends synthetic `DetectedTech` entries
    tagged `evidence_type="wappalyzer"`, `confidence="medium"`,
    `recommended=True`.
  - **LLM fallback add-on**: fires only when
    `self.tech_stack_llm_fallback and is_homepage and recommended_count == 0`
    (computed *after* the Wappalyzer merge). Reuses the existing
    `LLMExtractor.extract_by_type(html, "tech_stack")` +
    `self._convert_llm_results(url, "tech_stack", llm_data)` path — no new
    LLM-calling code, just a new, independent gate on the same existing
    machinery (previously only reachable via the generic `llm_mode` toggle).
- **`src/templates/web/pages/dashboard.html`** — two sub-toggles ("Use
  Wappalyzer library", "AI fallback if nothing detected") nested inside the
  `x-for="dt in allDataTypes"` loop, rendered via
  `x-if="dt.value === 'tech_stack' && dataTypes.includes('tech_stack')"`.
  The AI-fallback checkbox is disabled when `!llmAvailable`, mirroring the
  existing `llmMode` pattern.
- **`src/static/js/app.js`** — `quickScrape` Alpine component gained
  `techWappalyzer` / `techLlmFallback` state, included in the POST payload
  only when truthy (same pattern as `raw_only`/`llm_mode`).
- **`requirements.txt`** — added `setuptools<81` (see §5, real bug found
  during verification).

### Not changed (found during exploration, deliberately out of scope)

- **`src/data/wapp_converted_signatures.py`** and the merge at
  `tech_parser.py:83` — left as-is per decision above.
- **"Add Site" recurring-monitor modal** (`addSiteModal` in `app.js`, posts
  to `/api/tracked/add`) — turned out to run through a completely separate
  `tracked_domains` pipeline that doesn't even have `llm_mode`/`raw_only`
  today. Wiring the new toggles there would mean building a second,
  unreviewed integration path — deliberately skipped.
- **CLI** (`src/cli/commands/scrape.py`) / **MCP server** (`src/mcp_server.py`)
  — both POST a plain dict to `/scrape/execute`, validated by
  `ScrapeJobInput`; omitting the new fields there just keeps the `False`
  defaults, so nothing is broken. Adding explicit `--tech-wappalyzer` /
  `--tech-llm-fallback` flags is a cheap, isolated follow-up (see §7).

## 5. Bug found + fixed during verification

`python-Wappalyzer` imports `pkg_resources` at module load time. Recent
`setuptools` (≥81) dropped `pkg_resources` entirely, so the Wappalyzer
add-on silently failed (caught by the existing `try/except` in
`_extract_tech_stack`, logged as a warning, job still completed — but the
add-on did nothing) in both a fresh local venv and the Docker image built
from this repo's `Dockerfile`. Fixed by pinning `setuptools<81` in
`requirements.txt`. This is a real, permanent fix — not a local workaround —
since it also affects the standalone batch scripts, which import the same
`wappalyzer_runner.py`.

## 6. Verification performed

Environment: rebuilt `docker-compose.yml`'s `api`/`worker` images (`docker
compose up -d --build api worker`) against the existing `postgres`/`redis`
containers, using the real `OPENROUTER_API_KEY` already present in `.env`.

1. **Unit tests**: `pytest tests/unit/scraping/test_tech_parser.py
   tests/unit/workers/` — 18/18 pass (this also required installing
   `curl_cffi`, `python-Wappalyzer`, `browserforge` into the dev venv
   (`.venv-1`), which were missing — see §8).
2. **Wappalyzer add-on**, live job against `wordpress.org`
   (`tech_stack_wappalyzer: true, force_refresh: true`): homepage record
   kept its 5 curated detections (WordPress via `meta_generator`, Google
   Analytics/GTM via `script_url`, Google Fonts via `link_url`, WooCommerce
   via `inline_script`) and gained 5 new ones tagged
   `evidence_type: "wappalyzer"` (WordPress Site Editor, PHP, Gutenberg,
   WordPress Block Editor, MySQL) — correctly deduped against WordPress.
3. **LLM fallback, negative case**: same `wordpress.org` homepage (already
   has curated detections) with `tech_stack_llm_fallback: true` — confirmed
   it did **not** fire.
4. **LLM fallback, positive case**: `example.com` (zero curated detections)
   with `tech_stack_llm_fallback: true` — worker logs confirm a real
   OpenRouter call fired (`google/gemini-2.5-flash-lite`, 962 tokens); the
   LLM itself found nothing on that near-blank page, so (per existing,
   untouched `_convert_llm_results` logic) no extra record was created —
   correct behavior, not a bug.
5. **Homepage-only gating**: confirmed both add-ons stayed inactive on
   non-homepage pages across all test jobs.
6. **Content-cache interaction**: discovered (not a bug) that the
   pre-existing content-cache skips re-extraction entirely on unchanged
   pages (`cache_hit_skip_extraction` log) — `force_refresh: true` is
   required to re-trigger tech-stack extraction on a page already scraped
   with identical content.

## 7. Suggested next steps

- Commit these changes (currently uncommitted on
  `tech_detect_wapp+fingerprinter`).
- Optional: add `--tech-wappalyzer` / `--tech-llm-fallback` CLI flags
  (`src/cli/commands/scrape.py`) and matching MCP server params
  (`src/mcp_server.py`) for parity — low effort, `ScrapeJobInput` already
  supports it.
- Optional: decide whether the "Add Site" recurring-monitor modal should
  eventually support these toggles (would require adding `llm_mode`-style
  options to the separate `tracked_domains` pipeline first — out of scope
  here).
- Consider whether `TechStackMetadata` (`src/models/scraped_data.py`) should
  gain dedicated flat fields for Wappalyzer-only categories that currently
  have no home (`os`, `web_servers`, `programming_languages`) — today they
  still surface via the generic `detections` list, just not a dedicated
  column, same as several curated-only categories already behave.
- Manual QA in the dashboard UI itself (browser) — verification so far was
  via direct API calls (`curl` to `/api/scrape/execute`), not the Alpine.js
  checkbox UI in a browser.

## 8. Environment notes for whoever continues this

- Two Python venvs exist in this repo: `.venv` (minimal, ~80 packages) and
  `.venv-1` (~164 packages, has `pytest`/`fastapi`/etc. — this is the one to
  use for running tests locally).
- `.venv-1` now has `curl_cffi`, `python-Wappalyzer`, `browserforge`, and
  `setuptools<81` installed (previously missing, blocking any import of
  `src.workers.content_worker` or `wappalyzer_runner.py`).
- Local dev stack: `docker compose up -d` brings up `postgres` (host port
  `5435`), `redis` (host port `6382`), plus **containerized** `api` (port
  `3001`) and `worker` services built from this repo's `Dockerfile` — note
  `.env`'s `REDIS_URL=redis://localhost:7379` does NOT match the actual
  compose-mapped port `6382`; this only matters if running `api`/`worker` as
  local processes via `make dev`/`make worker` instead of the containerized
  versions (the containers use internal service DNS, unaffected by this
  mismatch).
- After any source change, the containerized `api`/`worker` need
  `docker compose up -d --build api worker` to pick it up (no volume mount —
  they run from an image baked at build time).

## 9. Browser QA session (2026-08-19) — done, found, and how to continue

Picked up §7's "Manual QA in the dashboard UI itself" item. Verified end-to-end
through the real Alpine.js checkbox UI (via the `playwright-cli` skill), not
just curl. No source files were changed in this session — findings below are
either confirmations or newly-found bugs to fix separately.

### Environment used

Root `docker-compose.yml` stack (not `docker-compose.local.yml`), rebuilt
against current working-tree code:
```
docker compose up -d --build api worker
```
Running at `http://localhost:3001` (postgres `:5435`, redis `:6382`). Logged
in as `admin@lakeb2b.internal` / `LakeB2B_admin!` (legacy auth defaults).

**Stateful change made to the local dev DB (not code)**: the default org's
Settings page now has an OpenRouter API key set (the same key already in
`.env`) and `llm_model` overridden to `google/gemini-2.5-flash-lite` — see
bug below for why. This lives in the `organizations` table in the
`tech_detect_2-postgres-1` container/volume; it will need re-doing if that
volume is ever reset, and doesn't need doing again if the same containers are
reused.

### Confirmed working (matches PRD §4 design intent)

1. Both nested toggles render correctly under "Tech Stack", gated by the
   `x-if` (only when Tech Stack is checked) — confirmed via live snapshot,
   not just code reading.
2. "AI fallback" checkbox starts disabled (no org OpenRouter key) and becomes
   enabled immediately after saving a key in Settings + reloading the
   dashboard — `llmAvailable` wiring confirmed live.
3. POST payload to `/api/scrape/execute` correctly includes
   `tech_stack_wappalyzer`/`tech_stack_llm_fallback` only when checked, and
   omits both keys entirely when unchecked (regression case, domain
   `iana.org`, job `10ddcc85-6f32-45fd-b47f-92fcd6c7b41a`) — confirmed via
   captured request bodies, not just code reading.
4. **Wappalyzer add-on, live**: domain `wordpress.com` (job
   `15a595b1-2017-4296-8187-b6437128abd0`) — homepage record kept its 12
   curated detections and gained exactly 5 more tagged
   `evidence_type: "wappalyzer"` (WordPress Block Editor, Facebook Pixel,
   Google Sign-in, PHP, MySQL), correctly deduped against curated names.
   Confirms `_merge_wappalyzer_detections` end-to-end via a real browser
   submission, not curl.
5. **LLM fallback gate, negative case**: same `wordpress.com` job — no
   `llm_call` log entry at all (curated already had recommended hits, so the
   gate correctly never even attempted a call).
6. **LLM fallback gate + real call, positive case**: domain `example.org`
   (job `b8a22975-9985-476a-a198-6c158c1f92c2`, zero curated hits) — gate
   fired, a real OpenRouter call succeeded (`google/gemini-2.5-flash-lite`,
   962 total tokens — same token count the PRD's original curl-based
   verification saw), found nothing on the near-blank page, so correctly
   produced no fabricated detections.
7. Job-status page polls via HTMX every 2s while running, as documented.

### New bug found (not yet fixed — separate from the two toggles' own logic)

**Both of the LLM extractor's model choices are stale on OpenRouter as of
today.** First attempt (`example.net`, job `c94775cd-0b84-4160-ab52-9003959dea87`)
failed outright:
```
llm_call_failed model=anthropic/claude-3.5-haiku error="404 No endpoints found..."
llm_model_fallback from_model=anthropic/claude-3.5-haiku to_model=google/gemini-2.0-flash-001
llm_call_failed model=google/gemini-2.0-flash-001 error="404 No endpoints found..."
llm_type_extraction_failed
```
The job still completed successfully (existing try/except resilience caught
it — not a crash), just silently produced no LLM-derived detections. The
OpenRouter key itself is valid and funded (confirmed via `GET
https://openrouter.ai/api/v1/key` — $21 credit remaining, not free-tier).

- Default model: `settings.llm_extraction_model` (`src/config/settings.py`),
  currently `anthropic/claude-3.5-haiku` — 404s.
- Hardcoded fallback: `_FALLBACK_MODEL = "google/gemini-2.0-flash-001"`
  (`src/services/llm_extractor.py:29`) — also 404s.
- Workaround used for this QA session: manually set the org's `llm_model` to
  `google/gemini-2.5-flash-lite` via the Settings UI, which the PRD's
  original verification also used successfully.
- **This affects every LLM-backed feature (the generic `llm_mode` toggle
  too, not just the new tech-stack fallback) for any fresh org that hasn't
  manually overridden the model** — worth a real fix (update the default +
  hardcoded fallback to currently-live OpenRouter model IDs) as its own
  small, separate task, not bundled into the tech-stack layered-detection
  work.

### Not yet done / how to continue

- **`/download/job/{id}/json` 404** (documented in §7 originally from code
  reading) was **not re-confirmed live** in this session — still just a
  code-level finding, not click-tested.
- The `wordpress.com` job (100 pages, `max_pages` slider not lowered before
  submit — there's no "force refresh" control in the UI either, see below)
  was still crawling in the background worker container when this QA pass
  was stopped. It's harmless to let finish or to leave — the homepage record
  (the only one relevant to the two add-ons, which are homepage-only) was
  already written early in the crawl and is what finding #4/#5 above are
  based on. No action required unless someone wants the full 100-page crawl
  data for other reasons.
- **Gap found, not a bug**: the dashboard's "Advanced options" panel has no
  "force refresh" checkbox — only reachable via the API's `force_refresh`
  param directly. This meant `wordpress.org`/`example.com` (already scraped
  in earlier curl-based verification) couldn't be reused for this browser
  pass without risking a content-cache skip — worked around by using
  never-before-scraped domains (`wordpress.com`, `example.org`, `example.net`,
  `iana.org`) instead. Not fixed; just noting why those domains were chosen.
- Containers are still running (`docker compose ps` shows
  `tech_detect_2-api-1`/`worker-1` up) with the current working-tree code
  baked in from the rebuild — reuse them for further QA rather than
  rebuilding again unless source files change.
- **B.1/B.2 (tracked-domains toggles design, `TechStackMetadata` schema
  design)**: fully designed, **not implemented**, and not yet confirmed with
  the user on scope. Recommendations on file (also mirrored in the local
  plan file used to drive this session,
  `go-through-this-application-harmonic-acorn.md`):
  - **B.1**: extend `AddSiteInput`/`TrackedDomain`/`addSiteModal`/
    `scheduled_scraper.py` with the two tech-stack toggles (it's plumbing —
    one shared `ContentWorker` implementation already handles both paths).
    Open scope question for the user: also fold in the pre-existing
    `llm_mode`/`raw_only`/`region`/`tier` gap in the same migration, or keep
    this narrowly to the two tech-stack toggles only.
  - **B.2**: recommend deferring dedicated `os`/`web_servers`/
    `programming_languages`/`widgets` fields on `TechStackMetadata` — data
    isn't lost today (already in `detections`), and `result_detail.html`
    already doesn't render 10 of the 14 existing dedicated fields, so adding
    4 more backend-only fields would repeat that pattern. Fix the results UI
    to render the generic `detections` list first if this is revisited.
  - Next step for whoever continues: get the user's decision on B.1's scope
    question, then implement B.1 + (if desired) fix the stale-LLM-model bug
    above as a separate small change, then decide whether to commit
    everything on this branch per §7's original "commit these changes" item.
