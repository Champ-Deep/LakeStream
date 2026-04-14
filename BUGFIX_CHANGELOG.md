# Scraping Bugfix Changelog

Date: 2026-04-14

## Summary

Full audit and fix of the scraping pipeline to eliminate stuck jobs, zombie browser processes, resource leaks, and silent failures. **8 files changed, 7 bugs fixed.**

---

## Bug #1: Zombie Browser Processes (CRITICAL)

**Files:** `src/scraping/fetcher/lake_playwright_fetcher.py`, `src/scraping/fetcher/lake_playwright_proxy_fetcher.py`, `src/scraping/fetcher/lake_lightpanda_fetcher.py`

**Problem:** All three Playwright-based fetchers created `BrowserContext` and `Page` objects but never explicitly closed them. Only `browser.close()` was called — and only on the happy path. If an exception occurred between context/page creation and `browser.close()`, the Chromium processes were orphaned as zombies consuming memory and CPU.

**Fix:** Wrapped browser/context/page lifecycle in `try/finally` blocks:
```python
context = None
page = None
try:
    context = await browser.new_context()
    page = await context.new_page()
    # ... work ...
finally:
    if page:
        await page.close()
    if context:
        await context.close()
    await browser.close()
```

**Impact:** Eliminates accumulation of zombie Chromium processes under sustained load or repeated errors.

---

## Bug #2: Heartbeat Never Called — Jobs Killed While Still Working (CRITICAL)

**Files:** `src/workers/base.py`, `src/workers/domain_mapper.py`, `src/queue/jobs.py`

**Problem:** `update_heartbeat()` was defined in `src/db/queries/jobs.py` but **never called anywhere**. The stale-job recovery cron (every 15 min) marks any job with no activity for 10 minutes as failed. Long-running crawls (domain mapping + content extraction) would routinely exceed 10 minutes, causing the cron to kill perfectly healthy jobs and leave them stuck in "running" on the UI.

**Root cause of stuck jobs like `eaff7cd7-ef08-4f4a-8be0-76ab3e15450e`.**

**Fix:**
- Added `heartbeat()` method to `BaseWorker` (throttled to once per 30s to avoid DB spam)
- `BaseWorker.fetch_page()` now calls `heartbeat()` before every fetch
- `DomainMapperWorker` calls heartbeat before and after the crawl phase
- `process_scrape_job` calls heartbeat after domain mapping completes
- Also passed `pool=pool` to `DomainMapperWorker` (was missing, so heartbeat had no DB connection)

**Impact:** Jobs will no longer be falsely killed by stale-job recovery while actively scraping.

---

## Bug #3: LightPanda Not Registered in Fetcher Factory (HIGH)

**File:** `src/scraping/fetcher/factory.py`

**Problem:** The factory only mapped `PLAYWRIGHT` and `PLAYWRIGHT_PROXY` tiers. When the escalation service chose `LIGHTPANDA` as the starting tier (cheapest), `create_fetcher(ScrapingTier.LIGHTPANDA)` silently fell back to `LakePlaywrightFetcher` — completely bypassing the cheaper/faster LightPanda path and wasting resources.

**Fix:** Added `ScrapingTier.LIGHTPANDA: LakeLightPandaFetcher` to the `_FETCHERS` registry.

**Impact:** LightPanda tier now actually used when configured, reducing cost and latency for initial fetches.

---

## Bug #4: Redis Connection Leak in EscalationService (HIGH)

**File:** `src/services/escalation.py`

**Problem:** `_check_session_health()` created a new Redis client with `redis.from_url()` and called `client.aclose()` inside the try block. If `client.get(key)` or `json.loads()` raised an exception, the close was skipped and the connection leaked. Under load (every domain check hits this), leaked connections would exhaust the Redis connection pool.

**Fix:** Moved `client.aclose()` into a `finally` block with a null-check:
```python
client = None
try:
    client = await redis.from_url(...)
    # ... work ...
finally:
    if client:
        await client.aclose()
```

**Impact:** Redis connections are always cleaned up, even on errors.

---

## Bug #5: Silent Exception Swallowing (MEDIUM)

**Files:** `src/scraping/fetcher/lake_playwright_proxy_fetcher.py`, `src/scraping/fetcher/lake_lightpanda_fetcher.py`, `src/services/session_manager.py`

**Problem:** Multiple `except Exception: pass` blocks silently swallowed errors:
- Proxy fetcher: networkidle timeout (line 153) — real errors hidden
- LightPanda fetcher: networkidle timeout (line 58) — real errors hidden
- Session manager: networkidle timeout (line 240) and auth selector check (line 274) — debugging impossible

**Fix:** Replaced all bare `pass` with `log.debug(...)` calls that log the error type and context without failing the operation:
```python
except Exception as e:
    log.debug("playwright_proxy_networkidle_timeout", url=url, error=str(e))
```

**Impact:** Errors are now visible in logs for debugging while remaining non-fatal.

---

## Bug #6: Timeout=0 Treated as Falsy (MEDIUM)

**Files:** `src/scraping/fetcher/lake_playwright_fetcher.py`, `src/scraping/fetcher/lake_playwright_proxy_fetcher.py`, `src/scraping/fetcher/lake_lightpanda_fetcher.py`

**Problem:** All fetchers used `timeout = options.timeout or settings.playwright_timeout_ms`. The `or` operator treats `0` as falsy, so an explicit `timeout=0` (meaning no timeout) would silently fall back to the 30-second default.

**Fix:** Changed to explicit None check:
```python
timeout = options.timeout if options.timeout is not None else settings.playwright_timeout_ms
```

**Impact:** Explicit timeout values (including 0) are now respected.

---

## Bug #7: No Hard Timeout on Scrape Jobs (HIGH)

**File:** `src/queue/jobs.py`

**Problem:** `process_scrape_job` had no overall timeout. The arq worker had a 2-hour timeout, but if the escalation service entered long wait loops (up to 10 min per escalation step) or the crawler hung on an unresponsive site, the job could run indefinitely. Combined with Bug #2 (no heartbeat), this meant jobs would hang forever showing "running" in the UI.

**Fix:**
- Added `asyncio.timeout(5400)` (90 minutes) wrapping the entire job body
- Added dedicated `TimeoutError` handler that marks the job as FAILED with a clear message: "Job timed out after 90 minutes"
- Also added `completed_at=datetime.now()` to the generic exception handler (was missing — failed jobs had no completion timestamp)

**Impact:** Jobs cannot hang indefinitely. Users see a clear timeout error instead of a perpetually "running" spinner.

---

## Bug #8: Missing TemplateConfig Import in BaseWorker (CRITICAL)

**File:** `src/workers/base.py`

**Problem:** During the base.py rewrite, the `from src.models.template import TemplateConfig` import was dropped. Every scrape job failed immediately with `name 'TemplateConfig' is not defined`.

**Fix:** Restored the missing import.

**Impact:** All scrape jobs could start executing again.

---

## Bug #9: PostgreSQL Type Inference Error in upsert_domain_metadata (CRITICAL)

**File:** `src/db/queries/domains.py`

**Problem:** The `upsert_domain_metadata` SQL used `$5` in conflicting type contexts:
- Integer: `successful_scrapes + $5`
- Float: `$5::float / $4`

PostgreSQL's prepared statement couldn't resolve `$5` as either `integer` or `double precision`. Every URL processed by ContentWorker hit this error, resulting in zero data extraction for every job.

**Fix:** Added explicit `::int` casts on `$4` and `$5` in all integer contexts so PostgreSQL can unambiguously resolve types.

**Impact:** Content extraction works — data is now saved to the database.

---

## Bug #10: Results Page — Data Type Dropdown Improvements (UX)

**Files:** `src/api/routes/web.py`, `src/templates/web/pages/results/browse.html`

**Problem:**
1. The data type dropdown was hardcoded with only 7 types — `document` and `extracted` types were missing
2. Raw `page` records (HTML dumps) cluttered the default unfiltered view (326 of 2077 results)
3. No indication of how many results each type has, making the dropdown less useful

**Fix:**
- Query distinct `data_type` values with per-type counts from the DB dynamically
- Render dropdown with counts: e.g. "Articles (1068)" instead of just "Articles"
- Exclude `data_type = 'page'` from the default (no-filter) view — users can still see them by explicitly selecting "Pages"
- Added badge colors for `document` (amber), `extracted` (cyan), and `pricing` (emerald) types

**Impact:** Dropdown is dynamic (auto-discovers new types), shows counts for quick orientation, and default view is cleaner.

---

## Bug #11: Parameter Type Ambiguity in recover_stale_jobs (CRITICAL)

**File:** `src/db/queries/jobs.py`

**Problem:** Same type inference bug as Bug #9 — `$2` used as both integer (`retry_count` column) and text (string concatenation in `error_message`). Worker crashed on startup every time.

**Fix:** Added `::int::text` casts for string concatenation contexts.

---

## Feature #1: Deep Keyword Search Across All Content

**Files:** `src/api/routes/web.py`, `src/templates/web/pages/results/browse.html`

**Problem:** Search only matched `title`, `url`, and `domain` fields. If a user searched "laptop", articles mentioning "laptop" in the body text wouldn't appear.

**Fix:**
- Added `metadata::text ILIKE $N` to the search query — casts the entire JSONB metadata to text for full-content matching
- Added relevance ranking: title matches appear first, then URL matches, then domain matches, then content-only matches
- Search placeholder updated to "Search all content..."

---

## Bug #12: Dropdown Filters Not Working in Browser (CRITICAL)

**File:** `src/templates/web/pages/results/browse.html`

**Problem:** The HTMX `hx-select="#results-container"` + `hx-swap="outerHTML"` pattern for filtering was unreliable in the browser. The server returned correct data (verified via curl), but the 77KB full-page response required HTMX to parse HTML, extract `#results-container`, and swap it — this was silently failing. When a user selected "Contacts" from the dropdown, the results table didn't update.

**Root cause:** HTMX's `hx-select` pattern works by parsing the full response as a DOM fragment, then querying it for the selector. With a 77KB response containing complex Alpine.js attributes and Jinja-rendered content, this parsing step was unreliable across browsers.

**Fix:** Replaced the entire HTMX form with Alpine.js direct navigation:
- Dropdowns use `x-model` + `@change="navigate()"` which builds a URL from all filter values and does `window.location.href = '/results?data_type=contact&...'`
- Search input uses `x-model` + `@keyup="debouncedSearch()"` with 500ms debounce
- All filter state is preserved in the URL — fully shareable, bookmarkable
- No HTMX DOM parsing needed — simple full page navigation

**Impact:** Dropdown filtering now works reliably. Selecting "Contacts" immediately navigates to `/results?data_type=contact` and shows only contacts.

---

## Feature #2: Filter-Aware CSV Export

**Files:** `src/api/routes/web.py`, `src/templates/web/pages/results/browse.html`

**Problem:** The "Download CSV" button always exported ALL data regardless of active filters. If user filtered by "Contacts", the CSV still contained all 2000+ records.

**Fix:**
- `/download/all` endpoint now accepts `domain`, `data_type`, and `q` query params
- Download link dynamically includes current filter params (e.g. `/download/all?data_type=contact&q=laptop`)
- Added a results toolbar inside `#results-container` showing count + active filters + CSV export button — this updates via HTMX when filters change

---

## Feature #3: Bulk CSV Upload (Admin Only)

**Files:** `src/services/bulk_upload.py` (NEW), `src/templates/web/pages/jobs/bulk.html` (NEW), `src/api/routes/web.py`, `src/templates/web/pages/jobs/list.html`

**Feature:** Admin can upload a CSV file containing URLs and scrape them all in bulk without crashing the system.

**Architecture (SOLID):**
- **S**: `parse_bulk_csv()` only parses/validates. `enqueue_bulk_jobs()` only creates DB records + enqueues. Web route only handles HTTP.
- **O**: Uses existing `ScrapeJobInput` + `create_job()` + arq enqueue — zero changes to scraping pipeline.
- **L**: Each bulk job is a standard `ScrapeJob` — workers can't tell it came from CSV vs. dashboard.
- **I**: Two-step UI: parse/preview (read-only) then confirm/enqueue (write).
- **D**: Bulk service depends on `create_job` + arq pool abstractions, not Redis/Playwright internals.

**User Flow:**
1. Admin navigates to `/jobs/bulk` (button on Jobs page, admin-only)
2. Uploads CSV file with URLs (drag-and-drop or file picker)
3. System parses CSV: validates domains, deduplicates, checks already-queued
4. Shows preview: N valid, M invalid, K duplicates, J already queued
5. Admin clicks "Start Bulk Scrape"
6. Jobs enqueued with staggered delays (30s default between each)
7. Shows results with links to each job

**Safety Guards:**
- Max 100 URLs per upload
- Max 5MB file size
- Stagger delay: configurable 15s/30s/60s between jobs (arq `_defer_by`)
- arq `max_jobs=10` enforces concurrency — excess jobs wait in Redis queue
- Dedup: skips domains with pending/running jobs from last hour
- Admin-only: `_require_admin()` gate on all 3 routes

**CSV Format:**
```
url
https://example.com
www.another-site.com
domain.org
```
Single column, header optional, protocols optional. Auto-detects `url`/`domain`/`website` column headers.

---

## Files Changed

| File | Changes |
|------|---------|
| `src/scraping/fetcher/factory.py` | Added LightPanda to fetcher registry |
| `src/scraping/fetcher/lake_playwright_fetcher.py` | try/finally cleanup, timeout fix |
| `src/scraping/fetcher/lake_playwright_proxy_fetcher.py` | try/finally cleanup, timeout fix, log networkidle |
| `src/scraping/fetcher/lake_lightpanda_fetcher.py` | try/finally cleanup, timeout fix, log networkidle |
| `src/workers/base.py` | Added heartbeat() method, call in fetch_page(), restored TemplateConfig import |
| `src/workers/domain_mapper.py` | Added heartbeat calls, pass pool to constructor |
| `src/queue/jobs.py` | asyncio.timeout wrapper, TimeoutError handler, heartbeat call, completed_at fix |
| `src/services/escalation.py` | Redis connection leak fix with finally block |
| `src/services/session_manager.py` | Replaced silent pass with debug logging |
| `src/db/queries/domains.py` | Explicit ::int casts to fix parameter type inference |
| `src/db/queries/scraped_data.py` | default=str safety net for json.dumps |
| `src/db/queries/jobs.py` | ::int::text casts in recover_stale_jobs SQL |
| `src/api/routes/web.py` | Dynamic type counts, deep JSONB search, relevance ranking, filter-aware CSV export, bulk upload routes |
| `src/templates/web/pages/results/browse.html` | Dynamic dropdown with counts, new badge colors, results toolbar with export |
| `src/services/bulk_upload.py` | **NEW** — CSV parsing, validation, dedup, staggered enqueue |
| `src/templates/web/pages/jobs/bulk.html` | **NEW** — Bulk upload form, preview table, enqueue results |
| `src/templates/web/pages/jobs/list.html` | Added "Bulk Upload" button for admin users |
