# LakeStream — Audit Findings & Completion Plan

**Branch audited:** `claude/merge-lakestream-v2-9Off5`
**Audit date:** 2026-05-05
**Verdict:** ~80% complete. One critical security gap, several integrity/UX gaps blocking a clean v1.

---

## 1. Audit summary

| Layer | Status | Notes |
|---|---|---|
| Scraping pipeline (fetchers, parsers, templates) | ✅ 95% | Mature, no stubs; only 3-tier doc mismatch |
| Services layer (LLM, signals, escalation, etc.) | ✅ 95% | All 18 services implemented; no dead code |
| Workers + queue + DB queries | ✅ 95% | Heartbeat, cron, all arq tasks wired |
| API routes — handlers | ✅ 90% | All routes present, almost all complete |
| API routes — **authorization** | ❌ **40%** | Critical leaks — see CRIT-1 |
| Migrations | ⚠️ 85% | Two pairs of duplicate-numbered files |
| CLI / MCP / browser extension | ✅ 90% | Functional, no stubs |
| Web UI | ✅ 90% | Pages render real data |
| Tests | ⚠️ 60% | Unit OK; integration skipped in CI; ~40 modules untested |
| Deployment | ⚠️ 80% | nixpacks lacks chromium install |
| Docs | ⚠️ 70% | README/PRD claim 3 tiers; only 3 exist but none is "basic HTTP" |

---

## 2. Critical findings

### CRIT-1 — Unauthenticated job data leak (HIGH severity)

`src/api/middleware/auth.py:29` declares `PUBLIC_PREFIXES = ("/api/", "/static/")`. The middleware exempts every `/api/*` route from auth enforcement; routes are only protected if their handler explicitly calls `Depends(get_current_user)` or reads `request.state.org_id` and 401s on miss.

**Two routes have no such gate:**
- `src/api/routes/scrape.py:82` — `GET /api/scrape/status/{job_id}` — anyone with a UUID reads job metadata, error messages, costs, page counts.
- `src/api/routes/scrape.py:108` — `GET /api/scrape/stream/{job_id}` — SSE stream of live status changes, no auth.

**Adjacent gaps** (`request.state` is read but never asserted as present, so failures silently pass `UUID(None)`):
- `src/api/routes/scrape.py:23,67` — execute/cancel; auth is implicit.
- `src/api/routes/exports.py:99` — CSV export reads `org_id` but doesn't filter the query, so a non-admin can download another user's CSV by guessing the job UUID.
- `src/api/routes/tracked.py:49` — `DELETE /tracked/{domain}` has no org check.

### CRIT-2 — Migration filename collisions

`src/db/migrations/`:
- `016_add_proxy_url_to_organizations.sql` + `016_add_user_id_and_admin.sql` (orthogonal — both apply, but the convention is broken)
- `017_disable_rls.sql` + `017_disable_rls_for_workers.sql` (the second is a superset; the first is redundant)

`migrate.py:36` uses `sorted(glob)` so both apply, but the convention should be cleaned up before more migrations land.

### CRIT-3 — Webhook callback is a stub

`src/api/routes/webhook.py:200` accepts inbound processed-data POSTs and returns `{"received_keys": [...]}` without persisting anything. The PRD's n8n integration assumes this works. Either implement persistence or remove the route.

---

## 3. High-priority gaps

### HIGH-1 — Documentation / PRD mismatch (3-tier story)
- `src/models/scraping.py:6` defines only 3 tiers: `LIGHTPANDA`, `PLAYWRIGHT`, `PLAYWRIGHT_PROXY`.
- `README.md:69` and the PRD describe a "Basic HTTP" tier costing $0.0001 with 200-500ms latency.
- Reality: cheapest tier is Lightpanda CDP at ~$0.001/req. Economics still beat Apify, but marketing copy is wrong.
- Fix: rewrite README's tier table to match reality, **or** add an `httpx`-based `BASIC_HTTP` tier and put it first in escalation.

### HIGH-2 — Org filtering inconsistency
RLS was disabled (`017_disable_rls.sql`) in favor of app-layer filtering. Most queries filter on `org_id` correctly, but enforcement is by convention. Routes that read `request.state.org_id` but don't 401 on missing values silently fall through to "default" org or `None`. A centralized `Depends(get_current_user)` requirement on every protected route would fix this in one shot.

### HIGH-3 — `nixpacks.toml` doesn't install chromium
`nixpacks.toml:9` runs only `playwright install-deps`, not `playwright install chromium`. The Dockerfile (`Dockerfile:18`) does install chromium; the worker Dockerfile (`Dockerfile.worker`) does not. If Railway uses nixpacks (no Dockerfile), every Playwright fetch fails. Either pick one path (Dockerfile-only, recommended) or add `playwright install chromium` to nixpacks.

### HIGH-4 — Bulk job submit silently swallows enqueue failures
`src/api/routes/web.py` (~line 500): the bulk-upload form's `try/except` around arq enqueue swallows the exception and creates orphan job rows in the DB that never get processed. Should rollback the DB row or surface the error.

### HIGH-5 — `scripts/test_3_tier_architecture.py` is incomplete
Proxy-priority chain test is truncated mid-function. Either finish or delete.

### HIGH-6 — Hardcoded admin password in `.env.example`
`.env.example:19` has `ADMIN_PASSWORD=LakeB2B_admin!`, and `migrate.py:59` uses that as the default if `ADMIN_PASSWORD` isn't set. On any deploy where the operator forgets to set it, the system boots with a known admin password that anyone reading the GitHub repo can use. Should require `ADMIN_PASSWORD` at boot (no default), or randomize it once on first boot and print it.

---

## 4. Medium-priority gaps

- **MED-1** Test coverage: ~40 production modules have zero tests (entire `src/queue/`, `src/services/domain_extractor.py`, `src/services/csv_exporter.py`, most workers). CI runs `-m "not integration"`, so integration tests never run.
- **MED-2** `src/services/sitemap_graph.py:29,33` use `...` literals — intentional `Protocol` body markers, but worth a docstring so future readers don't read them as stubs.
- **MED-3** Several `except: pass` blocks (`queue/jobs.py:283`, `escalation.py:168/202`, `proxy_health.py:96`) are intentional cleanup-error suppressors but should at minimum `log.debug("...")` so silent failures aren't invisible during incidents.
- **MED-4** No rate limiting on public routes (`/api/health`, `/api/templates`). Not a blocker for an internal tool but worth adding before public exposure.
- **MED-5** `docker-compose.local.yml:45` hard-codes `JWT_SECRET=local-dev-secret`. Fine for local; risk is operators copying this file into prod by accident.
- **MED-6** No SSE auth for `/scrape/stream/{job_id}` even after CRIT-1 is fixed — SSE doesn't carry cookies in some browsers/proxies; needs an explicit token-in-query-string pattern.
- **MED-7** `extension/popup/popup.js`: scheduled content-script injection may not persist across page reloads. LinkedIn reloads aggressively.
- **MED-8** `src/templates/web/pages/results/browse.html` was recently rewritten (per BUGFIX_CHANGELOG); good to lock in via a Playwright UI test that exercises filter dropdowns + CSV export.

---

## 5. Low-priority / polish

- **LOW-1** `pyproject.toml:74` declares `mypy --strict` but CI runs typecheck with `continue-on-error: true` — strict mode in name only.
- **LOW-2** `src/config/constants.py` — tier costs hardcoded; should pull from settings so per-org cost tracking is accurate when proxy pricing changes.
- **LOW-3** PRD filename has a trailing space (`ChampionInternalScraperPRD.md `).
- **LOW-4** `LICENSE` says MIT; confirm the upstream PRD also intends MIT before any external launch.

---

## 6. Completion plan

Three sprints. Each item is sized so it can be done in one commit.

### Sprint 1 — Security & integrity (≈1 day)

> **Goal:** make it safe to expose internally.

- [x] **S1.1 — Fix CRIT-1.** Added `require_org()` and `authorize_resource()` helpers in `src/api/middleware/auth.py`. Locked down `/api/scrape/*`, `/api/export/*`, and `DELETE /api/tracked/{domain}` to require org auth and 404 on cross-tenant access. 21 regression tests in `tests/unit/api/test_auth_enforcement.py`.
- [x] **S1.2 — Fix CRIT-3.** Implemented `POST /api/webhook/callback/{job_id}`: persists payloads as `scraped_data` rows with `data_type='webhook_callback'`, scoped to the job's org, with a 256 KiB payload cap. Added `WEBHOOK_CALLBACK` to the `DataType` enum. 7 regression tests in `tests/unit/api/test_webhook_callback.py`.
- [ ] **S1.3 — Fix HIGH-2.** Introduce a single `require_org()` helper that raises `HTTPException(401)` if `request.state.org_id` is `None`, and call it from every implicit-auth route. Eliminates silent-`None` code paths in `scrape.py:27-30` and similar.
- [ ] **S1.4 — Fix HIGH-6.** In `migrate.py:59`, drop the default — `if not os.environ.get("ADMIN_PASSWORD")` → log + skip the rehash. Update `.env.example` to mark `ADMIN_PASSWORD` as required.
- [ ] **S1.5 — Fix CRIT-2.** Rename migrations:
    - `016_add_proxy_url_to_organizations.sql` → `024_add_proxy_url_to_organizations.sql`
    - delete `017_disable_rls.sql` (subset of `017_disable_rls_for_workers.sql`)
    - rename `017_disable_rls_for_workers.sql` → `017_disable_rls.sql`
    - In `_migrations` table, `INSERT` a row for the renamed file so it's not re-applied. Add a release note.

### Sprint 2 — Documentation & deployment hygiene (≈0.5 day)

> **Goal:** stop misleading future operators.

- [ ] **S2.1 — Fix HIGH-1.** Rewrite `README.md` tier table to: Lightpanda (CDP, cheapest), Playwright (full Chromium), Playwright + residential proxy. Update the PRD's "Basic HTTP" copy to match. **Alternative:** add an httpx-based `BASIC_HTTP` tier (~50 LOC in `src/scraping/fetcher/lake_http_fetcher.py`) and register it in `factory.py`. **Recommend the doc-fix path** — basic HTTP rarely succeeds on modern B2B sites.
- [ ] **S2.2 — Fix HIGH-3.** Either (a) delete `nixpacks.toml` and force Dockerfile-based deploys on Railway, or (b) add `playwright install chromium` to nixpacks's `setup.cmds`. **(a) is simpler.**
- [ ] **S2.3 — Fix HIGH-4.** Rollback bulk-upload DB row on enqueue failure in `src/api/routes/web.py`.
- [ ] **S2.4 — Fix HIGH-5.** Finish or delete `scripts/test_3_tier_architecture.py`.

### Sprint 3 — Coverage & polish (≈2-3 days)

> **Goal:** make the codebase loud enough to debug under load.

- [ ] **S3.1 — MED-1.** Write tests for `src/queue/jobs.py::process_scrape_job` (mock arq + DB), `src/services/escalation.py::get_next_tier`, `src/services/llm_extractor.py::extract` (mock OpenAI). Get CI running integration tests with a Postgres+Redis service container.
- [ ] **S3.2 — MED-3.** Replace bare `except Exception: pass` blocks with `log.debug("cleanup_failed", op="...", error=str(e))` everywhere they appear.
- [ ] **S3.3 — MED-4.** Add slowapi or similar rate-limiting on `/api/health` and `/api/templates` if external exposure is on the roadmap.
- [ ] **S3.4 — MED-7.** Add a content-script reinjection check on `chrome.tabs.onUpdated` in `extension/background/service-worker.js`.
- [ ] **S3.5 — LOW-1.** Turn off `continue-on-error: true` for mypy in `.github/workflows/ci.yml` if strict typing is meant to be enforced.
- [ ] **S3.6 — LOW-2.** Move tier costs from `src/config/constants.py` to settings, allow per-org override.
- [ ] **S3.7 — LOW-3.** Rename PRD file to remove trailing space.

---

## 7. Recommended order

1. **Today** — `S1.1` only (auth gap on `/scrape/status/{job_id}`). 30-minute change; closes the only finding I'd call dangerous.
2. **This week** — rest of Sprint 1 + Sprint 2.
3. **Next week** — Sprint 3 in priority order.
