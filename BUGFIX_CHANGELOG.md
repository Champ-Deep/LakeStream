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

## Files Changed

| File | Changes |
|------|---------|
| `src/scraping/fetcher/factory.py` | Added LightPanda to fetcher registry |
| `src/scraping/fetcher/lake_playwright_fetcher.py` | try/finally cleanup, timeout fix |
| `src/scraping/fetcher/lake_playwright_proxy_fetcher.py` | try/finally cleanup, timeout fix, log networkidle |
| `src/scraping/fetcher/lake_lightpanda_fetcher.py` | try/finally cleanup, timeout fix, log networkidle |
| `src/workers/base.py` | Added heartbeat() method, call in fetch_page() |
| `src/workers/domain_mapper.py` | Added heartbeat calls, pass pool to constructor |
| `src/queue/jobs.py` | asyncio.timeout wrapper, TimeoutError handler, heartbeat call, completed_at fix |
| `src/services/escalation.py` | Redis connection leak fix with finally block |
| `src/services/session_manager.py` | Replaced silent pass with debug logging |
