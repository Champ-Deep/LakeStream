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
- [x] **S1.3 — Fix HIGH-2.** Implemented as part of S1.1 — `require_org()` lives in `src/api/middleware/auth.py` and is called from every implicit-auth route under `scrape.py`, `tracked.py`, and (via `_authorized_job_row`) `exports.py` + `webhook.py`.
- [ ] ~~**S1.4 — Fix HIGH-6.**~~ **Won't fix** by user direction (2026-05-05). The default admin password (`LakeB2B_admin!`) stays. Operators must rely on the standard rotation guidance in DEPLOYMENT.md instead.
- [x] **S1.5 — Fix CRIT-2.** Done:
    - `016_add_proxy_url_to_organizations.sql` → `024_add_proxy_url_to_organizations.sql`
    - deleted `017_disable_rls.sql` (it was a strict subset of `017_disable_rls_for_workers.sql`)
    - renamed `017_disable_rls_for_workers.sql` → `017_disable_rls.sql`
    - `migrate.py` now backfills `_migrations` for renamed files via `_RENAMED_MIGRATIONS` so existing DBs don't try to re-run them.
    - Verified end-to-end on both an existing DB (backfilled successfully) and a fresh DB (all 20 migrations apply cleanly).

### Sprint 2 — Documentation & deployment hygiene (≈0.5 day)

> **Goal:** stop misleading future operators.

- [x] **S2.1 — Fix HIGH-1.** Rewrote the README tier table to match reality (Lightpanda CDP, Playwright, Playwright + Residential Proxy). PRD left as historical context; README is now the source of truth.
- [x] **S2.2 — Fix HIGH-3.** Deleted `nixpacks.toml`; Railway now builds from the `Dockerfile` (which installs Chromium). `docs/DEPLOYMENT_GUIDE.md` updated with the rationale.
- [x] **S2.3 — Fix HIGH-4.** `src/services/bulk_upload.py` and the rerun-job handler in `src/api/routes/web.py` both now mark the orphan DB row as FAILED on enqueue failure (instead of swallowing). The user sees a real error in the dashboard rather than a perpetually-PENDING row.
- [x] **S2.4 — Fix HIGH-5.** Deleted `scripts/test_3_tier_architecture.py` — it referenced `ScrapingTier.BASIC_HTTP` which no longer exists, so it would crash on first run. `scripts/qa_production_validation.py` covers tier behavior.

### Sprint 3 — Coverage & polish (≈2-3 days)

> **Goal:** make the codebase loud enough to debug under load.

- [x] **S3.1 — MED-1 (partial).** Added `tests/unit/services/test_escalation_logic.py` (23 tests covering tier-chain transitions, escalation triggers, wait logic, and initial-tier decisions) and `tests/unit/queue/test_jobs.py` (4 tests covering the timeout path, generic-error path, heartbeat-task cleanup, and the timeout-constant invariant). LLM extractor tests and the CI integration-test setup are deferred to a later batch.
- [ ] **S3.2 — MED-3.** Replace bare `except Exception: pass` blocks with `log.debug("cleanup_failed", op="...", error=str(e))` everywhere they appear.
- [ ] **S3.3 — MED-4.** Add slowapi or similar rate-limiting on `/api/health` and `/api/templates` if external exposure is on the roadmap.
- [ ] **S3.4 — MED-7.** Add a content-script reinjection check on `chrome.tabs.onUpdated` in `extension/background/service-worker.js`.
- [ ] **S3.5 — LOW-1.** Turn off `continue-on-error: true` for mypy in `.github/workflows/ci.yml` if strict typing is meant to be enforced.
- [ ] **S3.6 — LOW-2.** Move tier costs from `src/config/constants.py` to settings, allow per-org override.
- [x] **S3.7 — LOW-3.** Renamed `ChampionInternalScraperPRD.md ` (trailing space) → `ChampionInternalScraperPRD.md`.

---

## 7. Status

**Sprint 1: complete** (S1.1, S1.2, S1.3, S1.5 done; S1.4 won't-fix per user direction).
**Sprint 2: complete** (S2.1–S2.4 done).
**Sprint 3: partial** (S3.1, S3.7 done; S3.2–S3.6 deferred — see notes in `plan.md` history).

All shipped code is verified against the `docker-compose.local.yml` stack — full unit suite at **238 passing**, plus the migration backfill exercised on both an existing-DB upgrade and a fresh-DB install.


---

## 8. Optional LLM extraction (via crawl4ai) + isolated extension fetcher

This section *replaces* the earlier draft of section 8. Two design constraints set by the user reframe the whole thing:

1. **LLM extraction is its own, separately-toggled feature.** No silent activation alongside the baseline scraper. A dedicated button in the UI, a dedicated endpoint server-side, a dedicated DB column to mark which jobs used it.
2. **Extension-based scraping is a new fetcher source that does not touch the existing tier chain.** Baseline scraping (Lightpanda → Playwright → Playwright+Proxy) keeps working exactly as today even if the extension code is broken or unshipped.

Both pieces are implemented as **plug-in strategies** behind already-existing protocols rather than edits to the working pipeline. SOLID compliance is the explicit organising principle, not a sticker.

---

### 8.1 Why crawl4ai, and what role it plays

[crawl4ai](https://github.com/unclecode/crawl4ai) (Apache-2.0) is a Playwright-based async crawler **with a built-in `LLMExtractionStrategy`** that already solves three of the seven problems we identified in the first audit pass:

| Pain point in our current LLM path | crawl4ai feature that solves it |
|---|---|
| Naive `markdown[:30000]` truncation drops relevant sections silently | "fit markdown" + BM25 noise filtering + chunking with `chunk_token_threshold` and `overlap_rate` |
| Per-page × per-type loop wastes 6× tokens (`content_worker:_llm_extract_every_type`) | `LLMExtractionStrategy` does single-pass schema extraction with chunking |
| No JSON-mode / structured outputs — relies on text parsing + retry | Pydantic `model_json_schema()` + LiteLLM provider strings give proper schema-mode for OpenAI, Anthropic, OpenRouter, Ollama |

What crawl4ai is **not**:

- It is not a free upgrade. It pulls Playwright too, so we have to reconcile its Playwright pin with our `playwright>=1.49.0`. Either pin to a version both libraries support or install crawl4ai as an optional extra (`pip install lakestream[llm]`) so non-LLM deploys aren't affected.
- It does not match our current org-level OpenRouter key resolution, prompt versioning, per-data-type schemas wired to dashboard CSV columns, or freeform-prompt extraction (`extract_freeform`). We keep all of those by **wrapping** crawl4ai behind our own interface, not by replacing our service.

**Decision:** crawl4ai becomes a swappable **`ExtractionBackend`**, not a replacement. The user picks which backend to use per-job (or per-org default). Existing `LLMExtractor` becomes the `OpenRouterDirectBackend` implementation; crawl4ai becomes `Crawl4AIBackend`. Adding a third backend later (Ollama-local, Gemini-direct, whatever) requires no edits to the pipeline.

---

### 8.2 The SOLID architecture

Two new protocols, both narrow:

```python
# src/scraping/contracts.py  (new file, single responsibility: define the seams)

from typing import Protocol, runtime_checkable
from src.models.scraping import FetchOptions, FetchResult
from src.models.extraction import ExtractionResult, ExtractionSchema

@runtime_checkable
class FetcherProtocol(Protocol):
    """Returns rendered page content for a URL. No extraction concern."""
    tier_id: str  # stable identifier ("lightpanda", "playwright", "extension"...)
    cost_estimate_usd: float

    async def fetch(self, url: str, options: FetchOptions) -> FetchResult: ...
    async def health_check(self) -> bool: ...

@runtime_checkable
class ExtractionBackend(Protocol):
    """Returns structured data given page content + a schema. No fetch concern."""
    backend_id: str  # "openrouter_direct", "crawl4ai", ...
    supports_freeform: bool
    supports_schema: bool

    async def extract_schema(
        self, html: str, url: str, schema: ExtractionSchema, instructions: str = ""
    ) -> ExtractionResult: ...

    async def extract_by_type(
        self, html: str, url: str, data_type: str, instructions: str = ""
    ) -> dict: ...

    async def extract_freeform(
        self, html: str, url: str, prompt: str
    ) -> dict: ...
```

These are the **two** seams the rest of the design hangs off. Everything that follows respects them:

- **S — Single responsibility.** Fetcher fetches; backend extracts; orchestrator orchestrates. No fetcher knows about LLMs. No backend knows about Playwright. The orchestrator (`process_scrape_job`) knows neither — it composes them via the protocols.
- **O — Open/closed.** Adding the extension fetcher OR the crawl4ai backend means writing one new file that implements one protocol and registering it. **Zero edits** to `lake_lightpanda_fetcher.py`, `lake_playwright_fetcher.py`, `lake_playwright_proxy_fetcher.py`, `escalation.py`, `content_worker.py`, or `process_scrape_job`.
- **L — Liskov.** Any `FetcherProtocol` instance is a valid argument anywhere any other is. Same for backends. No `isinstance` branches in callers.
- **I — Interface segregation.** Each protocol exposes ≤4 methods. No fetcher is forced to implement extraction; no backend is forced to know about cookies or proxies.
- **D — Dependency inversion.** `process_scrape_job` and `ContentWorker` import the protocols (`from src.scraping.contracts import ...`), not the concrete classes. Concrete classes are resolved through registries. Tests inject doubles.

#### Two registries, both feature-flag-gated

```python
# src/scraping/fetcher/registry.py — already exists conceptually, formalize it
_FETCHERS: dict[str, type[FetcherProtocol]] = {}

def register_fetcher(tier_id: str):
    def deco(cls): _FETCHERS[tier_id] = cls; return cls
    return deco

# src/scraping/extraction/registry.py — new
_BACKENDS: dict[str, type[ExtractionBackend]] = {}

def register_backend(backend_id: str):
    def deco(cls): _BACKENDS[backend_id] = cls; return cls
    return deco
```

Each new strategy decorates itself. The orchestrator calls `get_fetcher(tier_id)` / `get_backend(backend_id)`. Adding a strategy is purely additive.

**Feature flags:**

- `LLM_EXTRACTION_ENABLED=true|false` — controls registration of any LLM backend at all. Off → the LLM-extraction button is hidden in the UI and the relevant routes return 404. The baseline pipeline runs as today.
- `EXTENSION_FETCHER_ENABLED=true|false` — controls registration of the extension fetcher. Off → the tier chain is unchanged.
- `CRAWL4AI_BACKEND_ENABLED=true|false` — only meaningful when `LLM_EXTRACTION_ENABLED=true`; controls whether `Crawl4AIBackend` is registered alongside `OpenRouterDirectBackend`.

A deploy with all three flags off behaves byte-for-byte identically to the post-Sprint-1-2 codebase.

---

### 8.3 LLM extraction as a first-class, opt-in feature

#### 8.3.1 The user surface

A scrape job today is one form: domain + data types + tier override. We add a parallel button.

```
┌────────────────────────────────────────────────────────────┐
│  Scrape  example.com                                       │
│                                                            │
│  Data types  [✓] articles  [✓] contacts  [ ] pricing  ...  │
│  Tier        ⦿ auto  ○ playwright  ○ playwright+proxy     │
│                                                            │
│  ┌──────────────────┐   ┌─────────────────────────────┐    │
│  │ Start Scrape     │   │ ⚡ LLM-Augmented Scrape     │    │
│  └──────────────────┘   └─────────────────────────────┘    │
└────────────────────────────────────────────────────────────┘
```

Clicking "Start Scrape" → today's behavior, **untouched**.

Clicking "LLM-Augmented Scrape" → an additional confirmation dialog ("This will use AI extraction. Estimated cost: $X.XX based on N pages × ~$Y per page. Continue?") → POSTs to a **separate** endpoint with the same payload plus a backend selector.

Server-side, **two endpoints** instead of one to make the boundary impossible to miss:

| Endpoint | Behavior |
|---|---|
| `POST /api/scrape/execute` | Baseline scrape. `llm_mode` parameter is silently ignored on this endpoint after this change. Untouched. |
| `POST /api/scrape/execute-llm` | LLM-augmented scrape. Requires `extraction_backend` ∈ {`openrouter_direct`, `crawl4ai`}. Returns 404 when `LLM_EXTRACTION_ENABLED=false`. Records `llm_extraction_backend` on the job row so it's queryable. |

Routing diverges at the endpoint, not inside `process_scrape_job`. Two queue functions: `process_scrape_job` (existing, untouched) and `process_scrape_job_with_llm` (new). The arq worker registers both. The LLM variant calls the same fetcher chain for fetches but routes extraction through the chosen `ExtractionBackend`. **The baseline function never imports anything from the LLM module.**

#### 8.3.2 Schema additions (one migration)

```sql
-- 025_llm_extraction.sql
ALTER TABLE scrape_jobs ADD COLUMN extraction_backend TEXT;            -- null = baseline
ALTER TABLE scrape_jobs ADD COLUMN llm_prompt_version TEXT;
ALTER TABLE scrape_jobs ADD COLUMN llm_total_tokens INT DEFAULT 0;
ALTER TABLE scrape_jobs ADD COLUMN llm_cost_usd NUMERIC(10,6) DEFAULT 0;

CREATE TABLE llm_extraction_cache (
    content_hash TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    backend_id TEXT NOT NULL,
    model TEXT NOT NULL,
    data_types TEXT[] NOT NULL,
    result JSONB NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (content_hash, prompt_version, backend_id, model)
);
CREATE INDEX idx_llm_cache_age ON llm_extraction_cache(cached_at);
```

Cache key intentionally includes `backend_id` so switching backends doesn't return stale results from the other one.

#### 8.3.3 Concrete backend implementations

##### OpenRouterDirectBackend (refactor of current code)

`src/scraping/extraction/openrouter_direct.py`. Same code paths as today's `LLMExtractor` plus the four pending Sprint 4 improvements (rigid prompts, JSON-schema response_format, single-call multi-type, section-aware truncation, content-hash cache). Lives behind the protocol. Existing call sites (`scrape.py:/extract`, `mcp_server.py`) get re-pointed to it through the registry, but their public APIs don't change.

##### Crawl4AIBackend (new, optional)

`src/scraping/extraction/crawl4ai_backend.py`. Wraps `crawl4ai.AsyncWebCrawler` + `LLMExtractionStrategy`. Three responsibilities only:

1. Translate our `ExtractionSchema` → Pydantic-style schema → `model_json_schema()`.
2. Translate our org-level OpenRouter key + model setting → crawl4ai's `LLMConfig(provider=..., api_token=...)` LiteLLM string.
3. Translate crawl4ai's `result.extracted_content` → our `ExtractionResult`.

We do **not** use crawl4ai's `AsyncWebCrawler` for the **fetch** — we still fetch via our own tier chain (so the extension/Lightpanda/Playwright tiers stay in charge). We pass the already-fetched HTML as input via `input_format="markdown"` (after running our existing `_html_to_markdown`), letting crawl4ai handle just the chunking + LLM call. This is a deliberate design choice: it keeps fetch responsibility in fetchers, preserves our cost model, and avoids running two browsers in one job.

**Optional `pip extra`**:
```toml
# pyproject.toml
[project.optional-dependencies]
llm = ["crawl4ai>=0.6.0"]
```

Image deploys can choose to install or not. The backend's import is wrapped:

```python
try:
    from crawl4ai import AsyncWebCrawler, LLMExtractionStrategy, LLMConfig
    _CRAWL4AI_AVAILABLE = True
except ImportError:
    _CRAWL4AI_AVAILABLE = False
```

Registry guard: backend registers itself only if both `_CRAWL4AI_AVAILABLE` and `CRAWL4AI_BACKEND_ENABLED=true`.

#### 8.3.4 What about the freeform extract endpoint?

`POST /api/scrape/extract` (the dashboard's "ad-hoc extract" page) gets a backend selector parameter, defaulting to `openrouter_direct`. Users who want to try crawl4ai for a one-off extract pick it from a dropdown. No baseline behavior changes.

---

### 8.4 Extension-as-fetcher, fully isolated

#### 8.4.1 Design rule: invisible to the baseline

The baseline tier chain (`LIGHTPANDA → PLAYWRIGHT → PLAYWRIGHT_PROXY`) **never** looks at the extension. Even with `EXTENSION_FETCHER_ENABLED=true`, the extension is **only** chosen when the user explicitly opts in:

- Per-job: `POST /api/scrape/execute` with `tier="extension"` (and the calling user has an active subscriber)
- Per-domain: an `extension_preferred` flag on `tracked_domains` → `decide_initial_tier` peeks at it before falling through to its existing logic
- Per-org default: `organizations.default_fetcher_tier` (new optional column, null = today's behavior)

Auto-escalation on a job that started with the extension can fall *back* to Lightpanda/Playwright if the extension times out, but auto-escalation on a baseline job **never** escalates *up* into the extension. The flow is one-directional, from extension → server-side tiers, never the reverse. This guarantees that operators who haven't installed the extension are never blocked waiting for someone who has.

#### 8.4.2 Implementation as a `FetcherProtocol`

`src/scraping/fetcher/lake_extension_fetcher.py` — a single 200-LOC class implementing `FetcherProtocol.fetch()`. Internally:

```
1. Check there's a live subscriber for this org (last_heartbeat_at < 60s ago).
2. Push a fetch request onto Redis list `ext:fetch:{org_id}`.
3. Block on a per-request response key with a 30s timeout.
4. On response → assemble FetchResult. On timeout → raise FetchTimeout.
```

`escalation.get_next_tier()` is **not** modified. Instead, when `tier="extension"` is the explicit start, the orchestrator uses a **dedicated tier order** for that job — `[EXTENSION, PLAYWRIGHT, PLAYWRIGHT_PROXY]` — built by a separate function `_build_tier_order_with_extension()`. The default tier order helper is untouched.

Crucially: **the extension never serves a job started without `tier="extension"`**. There is no path by which an unrelated baseline scrape suddenly fires off requests to a user's browser. The only Redis key the extension reads from is `ext:fetch:{org_id}`, and it's only ever written to by jobs that explicitly opted in.

#### 8.4.3 Server-side endpoints (all auth-gated, follow S1.1 pattern)

| Endpoint | Purpose |
|---|---|
| `POST /api/extension/register` | Register a browser, return a session id |
| `GET /api/extension/poll` | Long-poll for pending fetch requests for this subscriber's org |
| `POST /api/extension/fetch_result/{request_id}` | Submit HTML/cookies-snapshot back |
| `PATCH /api/extension/preferences` | `domains_opted_in`, `daily_quota`, `paused_until` |
| `POST /api/extension/heartbeat` | Light keepalive (used by `decide_initial_tier` to know if the extension is online) |

Cross-tenant isolation is the same as everywhere else: `require_org()` + `authorize_resource()` from S1.1. A subscriber for org A cannot see fetch requests for org B even if they guess the request UUID.

#### 8.4.4 Schema additions

```sql
-- 026_extension_subscribers.sql
CREATE TABLE extension_subscribers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    browser_fingerprint TEXT NOT NULL,
    last_heartbeat_at TIMESTAMPTZ,
    domains_opted_in TEXT[] DEFAULT '{}',
    cookie_allowlist JSONB DEFAULT '{}'::jsonb,  -- per-domain cookie allowlist
    daily_fetch_quota INT DEFAULT 200,
    fetches_today INT DEFAULT 0,
    paused_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_ext_subs_org_heartbeat ON extension_subscribers(org_id, last_heartbeat_at DESC);

-- 027_extension_job_pref.sql (optional per-org default)
ALTER TABLE organizations ADD COLUMN default_fetcher_tier TEXT;  -- null = baseline
```

#### 8.4.5 Extension-side changes

The existing `extension/` directory's content scripts (`linkedin-sales-nav.js`, `apollo.js`, `auto-detect.js`) **stay**. They serve a different use case — manual on-page scrape that POSTs to `/api/ingest`. The new fetcher capability is additive:

- **New file** `extension/background/server-fetch.js` — service-worker module that long-polls `GET /api/extension/poll`, opens hidden tabs, captures HTML, posts back. Independent of the existing content scripts.
- **New popup panel** in `extension/popup/popup.html` — "Server Fetch" tab showing pending requests with allow/deny/always-allow-domain.
- **No changes** to `auto-detect.js`, `linkedin-sales-nav.js`, `apollo.js`, or the existing `/api/ingest` flow. Manual scrape keeps working identically.

#### 8.4.6 Trust & safety controls (ship with v1, not later)

| Control | Where |
|---|---|
| Default-deny per-domain consent | popup; first request from a new domain pops a notification |
| Cookie allowlist per domain | `extension_subscribers.cookie_allowlist`; only listed cookie names are sent back |
| Daily fetch quota with auto-pause | server-side enforced on `/fetch_result` POST |
| One-click "pause for 1 hour" / "pause until I unpause" | popup |
| All fetch activity in an audit log accessible to the user | new `/account/extension-activity` page |
| `<all_urls>` permission stays — but server requests for a domain not in `domains_opted_in` are dropped server-side | poll endpoint filters before sending |

---

### 8.5 What this design buys us — explicit non-effects on the baseline

This is the verification checklist for "does it actually not collide?":

| If we did this... | Baseline scraper behavior |
|---|---|
| `LLM_EXTRACTION_ENABLED=false`, `EXTENSION_FETCHER_ENABLED=false` | identical to today (post Sprint 1+2) |
| Only `LLM_EXTRACTION_ENABLED=true` | baseline `/api/scrape/execute` unchanged; new `/execute-llm` exists; cron jobs unchanged; tier chain unchanged |
| Only `EXTENSION_FETCHER_ENABLED=true` | baseline `/api/scrape/execute` unchanged; tier chain extended *only* for jobs that explicitly request `tier="extension"` |
| crawl4ai installed but `CRAWL4AI_BACKEND_ENABLED=false` | backend unregistered; only `openrouter_direct` available; no behavior change |
| crawl4ai missing entirely (no pip extra installed) | `Crawl4AIBackend` import fails silently at registration; UI hides the option |
| Extension subscriber dies mid-fetch | per-request 30s timeout fires → orchestrator falls back to next tier → job completes |
| Extension SSE endpoint crashes | `extension_subscribers.last_heartbeat_at` ages out → `decide_initial_tier` skips extension → baseline path used |

If the baseline ever breaks because of these features, *that's a regression bug* — not a design tradeoff.

---

### 8.6 Sprint plan (revised)

#### Sprint 4 — Extraction backend protocol + crawl4ai (1.5 weeks)

> **Goal:** opt-in LLM extraction lives behind a swappable interface and uses crawl4ai when chosen.

- [ ] **S4.1 — Define contracts.** Create `src/scraping/contracts.py` with `FetcherProtocol` and `ExtractionBackend`. Add a guard test that asserts both protocols are `runtime_checkable` and that every existing fetcher class structurally satisfies `FetcherProtocol`.
- [ ] **S4.2 — Refactor existing LLM service into `OpenRouterDirectBackend`.** Move the contents of `src/services/llm_extractor.py` into `src/scraping/extraction/openrouter_direct.py`. Keep the `LLMExtractor` class name as a thin re-export so external callers don't break. Register it with `@register_backend("openrouter_direct")`.
- [ ] **S4.3 — Rigid prompts (carry-over from previous draft of S4).** Versioned `Prompt` dataclasses, evidence-anchored language, examples, self-check footer. `prompt_version` constant lives in the backend module and is written to `scrape_jobs.llm_prompt_version`.
- [ ] **S4.4 — JSON-schema mode.** OpenRouter `response_format={"type":"json_schema", strict:true}` for capable models, `json_object` fallback, plain text last-ditch. Capability registry in the backend module.
- [ ] **S4.5 — Single-call multi-type extraction.** `extract_all_types_for_page()` returns the union schema in one LLM call instead of looping per type. Wired into the LLM-only orchestrator path (`process_scrape_job_with_llm`); the baseline orchestrator never calls it.
- [ ] **S4.6 — Section-aware truncation.** Replace `markdown[:30000]` with a relevance-scored section packer; signal dropped sections with a marker string.
- [ ] **S4.7 — Content-hash extraction cache.** Migration `025_llm_extraction.sql` (cache table + `scrape_jobs` LLM bookkeeping columns). New `src/db/queries/llm_cache.py`. Cache key includes `backend_id` so the two backends don't poison each other.
- [ ] **S4.8 — `Crawl4AIBackend`.** New file. Optional dependency via `[project.optional-dependencies] llm = ["crawl4ai>=0.6.0"]`. Soft-import + capability flag. Translates our schema to Pydantic, our org key to LiteLLM provider string. **Reuses existing fetched HTML** rather than spawning a second browser.
- [ ] **S4.9 — `process_scrape_job_with_llm`.** New arq task. Fetches via the existing tier chain (no changes there). Routes extraction through the chosen backend. Records `llm_total_tokens` and `llm_cost_usd` on the job row.
- [ ] **S4.10 — Dedicated endpoints + UI.** `POST /api/scrape/execute-llm`, `extraction_backend` parameter, separate dashboard button with cost preview dialog, separate `/jobs/new-llm` page. **Zero edits** to the existing scrape form.
- [ ] **S4.11 — Tests.**
    - Both backends pass the same `ExtractionBackend` protocol contract (parametrize the same test against both).
    - With `LLM_EXTRACTION_ENABLED=false`, `/execute-llm` returns 404 and the registry has no LLM backends.
    - `OpenRouterDirectBackend` cache hit/miss behavior.
    - `Crawl4AIBackend` translates our schema to Pydantic correctly (mock the `arun` call).
    - Baseline `process_scrape_job` doesn't import anything from the LLM module (assert via `importlib`).

#### Sprint 5 — Extension fetcher (2 weeks, requires legal review first)

> **Goal:** the extension can serve fetches when the user explicitly opts in, and can never affect baseline scrapes.

- [ ] **S5.1 — Migrations.** `026_extension_subscribers.sql`, `027_extension_job_pref.sql`.
- [ ] **S5.2 — `LakeExtensionFetcher`.** Implements `FetcherProtocol`. Redis push + poll + 30s timeout. Independent Redis key namespace (`ext:fetch:*`).
- [ ] **S5.3 — Dedicated tier order helper.** `escalation._build_tier_order_with_extension()` (separate from `_build_tier_order`) — used only when a job opts in.
- [ ] **S5.4 — Server endpoints.** Five auth-gated routes per the table above. Each follows the S1.1 pattern (require_org / authorize_resource).
- [ ] **S5.5 — Subscriber registration + heartbeat in extension service worker.** `extension/background/server-fetch.js`. Independent of existing content scripts.
- [ ] **S5.6 — Hidden-tab fetch in extension.** `chrome.windows.create({ state: "minimized", focused: false })` (or `chrome.offscreen` API where supported) to avoid disrupting active browsing.
- [ ] **S5.7 — Popup "Server Fetch" tab.** Pending requests, allow/deny, always-allow-domain, paused state, daily quota meter.
- [ ] **S5.8 — Trust & safety.** Default-deny per domain, cookie allowlist UI per domain, audit log page at `/account/extension-activity`.
- [ ] **S5.9 — Tests.**
    - With `EXTENSION_FETCHER_ENABLED=false`, `_build_tier_order` is unchanged and the extension fetcher is unregistered.
    - Baseline tier order test from S3.1 still passes byte-for-byte.
    - Mock-extension round-trip via Redis (subscriber registers → server enqueues → mock client posts result → fetcher returns FetchResult).
    - Quota enforcement test (subscriber over quota → server drops result → caller receives FetchTimeout → next tier picks up).
    - Cross-org isolation test (subscriber for org A can't see requests for org B even with the right request UUID).
    - Cookie-allowlist test (response with disallowed cookie keys is sanitized server-side before storage).

#### Sprint 6 — Polish & shipping (3-5 days)

- [ ] **S6.1 — Stats panel.** Per-org tile showing: jobs by extraction backend (baseline vs LLM), jobs by tier (extension vs Lightpanda/Playwright/Proxy), LLM tokens/cost trend.
- [ ] **S6.2 — Migration smoke tests** on a fresh DB (mirror the S1.5 process used in this PR).
- [ ] **S6.3 — End-to-end demo scripts.** (a) baseline scrape — works exactly as before; (b) LLM-augmented scrape with `openrouter_direct` — populated row + `llm_cost_usd` shown; (c) LLM-augmented scrape with `crawl4ai` — same output shape; (d) extension scrape — populated row + audit log entry.
- [ ] **S6.4 — Docs.** New `docs/EXTRACTION_BACKENDS.md` (when to pick which, costs, examples). New `docs/EXTENSION_SCRAPING.md` (consent model, what cookies are sent, operator FAQ). Update `docs/DEPLOYMENT_GUIDE.md` with the three new feature flags.

---

### 8.7 Recommended sequencing & gates

1. **Ship Sprint 4 first** — pure server work, zero UI risk for non-LLM users, immediate cost win for LLM users. Measure: token spend per 100-page job before vs after; expected 60-80% reduction.
2. **Pause and get a privacy/legal review before Sprint 5.** The extension architecture is technically sound but cookies + cross-org operator scraping introduce non-engineering questions.
3. If Sprint 5 ships, **internal-only first** — only operators inside Lake B2B's own org can be subscribers. Validate the model for a quarter before exposing to client orgs.

### 8.8 Open questions

1. **Pin compatibility:** what Playwright version does the latest `crawl4ai` require, and does it overlap with our `>=1.49.0`? (Resolve before S4.8 lands. If no overlap, we either bump our pin or ship `crawl4ai` only via Docker variant.)
2. **Per-job cost ceiling for LLM-augmented scrapes:** what's the largest auto-approved spend per job? Above that → require human approval in the dialog. Drives the Sprint 4 cost-preview UI.
3. **Crawl4AIBackend default model:** ours defaults to `anthropic/claude-3.5-haiku`. Crawl4ai uses LiteLLM strings. Same model, different provider string — confirm the routing.
4. **Extension's `default-deny vs always-allow-domain` policy** — am I right that always-allow-per-domain is acceptable, or do we want per-fetch approval forever?
5. **Backend choice persistence:** is the user's choice (openrouter_direct vs crawl4ai) per-job, per-org default, or both? My plan assumes both — confirm.
