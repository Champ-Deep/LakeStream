# LakeStream × Scrapling Integration — CHAMP One-Pager + PRD

**Mode:** Product (Internal Tooling)
**Date:** February 27, 2026
**Owner:** Champions Group Engineering / Lake B2B
**Status:** Draft — Post-CHAMP Analysis

---

## CHAMP One-Pager

### C — Customer

**Primary Persona:** The LakeStream engineering team and Lake B2B's internal ops — a team that needs to scrape thousands of B2B domains daily for contact enrichment, tech stack detection, and intent signals, without paying per-call Firecrawl API fees or being locked into an external vendor's uptime and roadmap.

**Core Pain Point:** "We're paying Firecrawl for something we could own. Every API call is a cost center, we can't customize the extraction pipeline, and when Firecrawl has downtime or changes their API, our entire job queue stalls."

**Key Segments:**

- **Daily scrape pipeline** — high-volume, cost-sensitive batch jobs (contact enrichment, domain mapping)
- **Real-time intent signals** — lower volume but latency-sensitive (pricing changes, hiring spikes, tech stack shifts)
- **Discovery crawls** — recursive domain mapping for new prospects via LakeCurrent search results

### H — Hypothesis

> "We believe LakeStream can fully replace Firecrawl by integrating Scrapling as the core fetching/parsing engine, eliminating vendor dependency and per-call costs, while maintaining or improving scrape success rates — within 2-3 weeks of engineering effort."

**Riskiest Assumption:** That Scrapling's stealth capabilities match or exceed what Firecrawl's managed infrastructure provides for anti-bot bypass, without needing to replicate their full cloud infrastructure.

**Validation Approach:** Benchmark Scrapling against 50 representative B2B domains from existing job history — compare success rate, speed, and content quality vs. current Firecrawl + HTTP fallback results.

### A — Approach

| Phase | What | Timeline | Cost |
|-------|------|----------|------|
| 1 | Scrapling fetcher integration + factory pattern | Week 1 | Engineering time only |
| 2 | Parser upgrade + FirecrawlService retirement | Week 2 | Engineering time only |
| 3 | Benchmark, harden, deploy to Railway staging | Week 3 | Engineering time only |

**Personalization Strategy:** The fetcher factory pattern (`src/scraping/fetcher/factory.py`) already abstracts tier selection — Scrapling slots in as `ScraplingFetcher` and `ScraplingStealthFetcher` without touching the escalation service, cost tracker, or job queue. Zero blast radius.

### M — Market / Metrics

**Cost Savings:** Firecrawl API costs → $0 per call (self-hosted, OSS dependency only)

**Key Metrics:**

| Metric | Current (Firecrawl) | Target (Scrapling) | Measurement |
|--------|---------------------|--------------------|-------------|
| Scrape success rate | ~85% (estimated) | ≥90% | % of jobs returning valid content |
| Avg latency per page | ~3-5s (API round-trip) | ≤2s (local Playwright) | p50 duration_ms from FetchResult |
| Monthly infrastructure cost | Firecrawl fees + infra | Infra only | Firecrawl invoice → $0 |
| Blocked/captcha rate | ~15-20% | ≤10% | FetchResult.blocked + captcha_detected |

### P — Pivot

**Pivot Triggers:** Scrapling success rate drops below 80% on the target domain set, OR Scrapling's maintenance/release cadence stalls (check GitHub pulse).

**Plan B:** If Scrapling underperforms on stealth specifically, keep Scrapling for basic/fast fetching but layer in a lightweight proxy service (ScraperAPI, Bright Data SDK) as a Tier 3 alternative — still cheaper than Firecrawl.

**Kill Criteria:** If integration takes >4 weeks due to incompatible async patterns or introduces instability in the job queue, revert and evaluate crawl4ai or raw Playwright with stealth plugins instead.

**Milestone Roadmap:**
MVP (Week 3) → Staging benchmark passes → Production deploy → Firecrawl fully retired (Week 4)

---
---

# Product Requirements Document: LakeStream Scrapling Integration

**Version:** 1.0 — MVP
**Author:** Champions Group Engineering
**Date:** February 27, 2026
**Status:** Draft — Post-CHAMP Brainstorm

---

## 1. Problem Statement

LakeStream currently depends on Firecrawl as its scraping backbone. While the codebase already has a `FirecrawlService` wrapper marked as "transitionary," the actual migration away from Firecrawl has stalled. Every scrape job that routes through Firecrawl's API incurs per-call costs, introduces a single point of failure outside the team's control, and limits the ability to customize extraction behavior for Lake B2B's specific B2B data schema (contacts, tech stacks, pricing pages, job postings).

The `BrowserFetcher` and `ProxyFetcher` classes in `src/scraping/fetcher/` handle Playwright directly but lack anti-detection stealth — meaning they get blocked on a significant portion of protected B2B sites. Scrapling solves this by providing battle-tested stealth automation, smart selectors, and an adaptive engine that handles bot detection without the team needing to maintain their own fingerprinting and evasion logic.

### 1.1 Who Has This Problem?

**Primary Persona:**
- **Archetype:** "The LakeStream Pipeline Operator"
- **Role:** DevOps / backend engineer running the arq worker fleet on Railway
- **Behavior:** Monitors job success rates on the HTMX dashboard, manually investigates blocked domains, escalates proxy costs when Tier 3 usage spikes
- **Quote:** "Half my time is debugging why Firecrawl returned garbage for a domain that worked last week."

**Secondary Segments:**
- **Data team:** Needs clean, structured extraction output — cares about parsing quality more than fetching mechanics
- **Product/sales ops:** Uses the dashboard and webhook integrations — cares about throughput and reliability, not implementation details

### 1.2 Current Alternatives & Their Failures

| Alternative | What It Does Well | Where It Fails |
|------------|-------------------|----------------|
| Firecrawl API | Managed infrastructure, decent stealth | Cost per call, vendor lock-in, no customization, outages outside your control |
| Raw Playwright (current BrowserFetcher) | Full control, already integrated | No stealth — gets blocked by Cloudflare, Akamai, DataDome, PerimeterX |
| Crawl4AI | Open source, LLM-friendly | Less mature, smaller community, weaker selector API than Scrapling |
| Do nothing | No migration effort | Continued Firecrawl dependency and costs, blocked by anti-bot on growing % of sites |

---

## 2. Product-Market Fit Hypothesis

> "We believe the LakeStream platform will achieve ≥90% scrape success rate and eliminate Firecrawl API costs by integrating Scrapling as the core fetching engine, because Scrapling's stealth browser automation and adaptive selectors directly address the two biggest pipeline failures: bot detection blocks and brittle CSS selectors."

**Riskiest Assumption:** Scrapling's `StealthyFetcher` and `PlayWrightFetcher` will handle the anti-bot systems commonly deployed on B2B company websites (Cloudflare, Akamai, Sucuri) without needing additional proxy infrastructure.

**Validation Method:** Run the integration against the 50 most-scraped domains in the existing `domain_metadata` table. Compare `blocked` rate, content length, and extraction completeness vs. current pipeline.

**Success Signal:** ≥90% success rate across the 50-domain benchmark set, with equal or better content quality.

**Failure Signal:** Success rate <80%, or >5 domains that previously worked now fail under Scrapling.

---

## 3. Solution Overview

### 3.1 Core Value Proposition

Replace Firecrawl's managed API with Scrapling's open-source scraping framework as the fetching engine inside LakeStream — keeping the entire existing architecture (FastAPI, arq queue, PostgreSQL, escalation service, cost tracker, HTMX dashboard) intact while gaining stealth automation, better selectors, and zero per-call API costs.

### 3.2 Key Features — MVP Scope

| Feature | Priority | Description | Rationale |
|---------|----------|-------------|-----------|
| `ScraplingFetcher` class | P0 | New fetcher using Scrapling's `Fetcher` for fast HTTP requests with smart response parsing | Replaces `HttpFetcher` with richer response objects |
| `ScraplingStealthFetcher` class | P0 | New fetcher using Scrapling's `StealthyFetcher` for Playwright-based stealth browsing | Replaces `BrowserFetcher` + `ProxyFetcher` with built-in anti-detection |
| Factory pattern update | P0 | Update `create_fetcher()` in `factory.py` to route tiers to new Scrapling-backed classes | Zero-change integration with existing escalation and cost tracking |
| `Adaptor`-based parsing | P1 | Use Scrapling's `Adaptor` class in parsers for CSS/XPath/text-based selection with auto-matching | Improves extraction resilience when sites redesign |
| `FirecrawlService` retirement | P1 | Remove transitionary wrapper, point `map_domain` and `scrape_page` directly to native services | Eliminates dead code path and Firecrawl import |
| Benchmark harness | P1 | Script to compare Scrapling vs. current pipeline across N domains | Validates hypothesis before production deploy |

### 3.3 Explicitly Out of Scope for MVP

- **Scrapling MCP server integration** — Deferred to M2; interesting for AI-agent workflows but not needed for Firecrawl replacement
- **Camoufox browser support** — Scrapling supports Camoufox for maximum stealth; defer unless Cloudflare blocks persist after MVP
- **Scrapling's `PlayWrightFetcher` with CDP** — Start with `StealthyFetcher`; only bring in raw Playwright fetcher if specific JS rendering needs arise
- **Custom fingerprint profiles** — Scrapling supports custom browser profiles; defer to M1 tuning phase
- **Async connection pooling for Scrapling** — Scrapling handles its own sessions; only optimize if benchmark reveals bottleneck

---

## 4. Integration Architecture

### 4.1 Current Architecture (What Stays)

```
FastAPI → arq/Redis Queue → Worker Pool → EscalationService → FetcherFactory → [Fetchers] → Parsers → PostgreSQL
                                              ↓
                                         CostTracker
                                         RateLimiter
```

Everything above stays identical. We are **only replacing what's inside the `[Fetchers]` box.**

### 4.2 New Fetcher Layer

```
FetcherFactory.create_fetcher(tier)
  ├── ScrapingTier.BASIC_HTTP      → ScraplingFetcher        (Scrapling Fetcher, fast HTTP)
  ├── ScrapingTier.HEADLESS_BROWSER → ScraplingStealthFetcher (Scrapling StealthyFetcher)
  └── ScrapingTier.HEADLESS_PROXY   → ScraplingProxyFetcher   (StealthyFetcher + proxy config)
```

### 4.3 File-Level Change Map

| File | Action | Description |
|------|--------|-------------|
| `src/scraping/fetcher/scrapling_fetcher.py` | **CREATE** | New: Scrapling `Fetcher`-backed HTTP fetcher |
| `src/scraping/fetcher/scrapling_stealth_fetcher.py` | **CREATE** | New: Scrapling `StealthyFetcher`-backed browser fetcher |
| `src/scraping/fetcher/scrapling_proxy_fetcher.py` | **CREATE** | New: StealthyFetcher + residential proxy config |
| `src/scraping/fetcher/factory.py` | **MODIFY** | Update `_FETCHERS` dict to point to Scrapling classes |
| `src/scraping/fetcher/http_fetcher.py` | **KEEP** | Keep as fallback; rename to `legacy_http_fetcher.py` |
| `src/scraping/fetcher/browser_fetcher.py` | **KEEP** | Keep as fallback; rename to `legacy_browser_fetcher.py` |
| `src/scraping/fetcher/proxy_fetcher.py` | **KEEP** | Keep as fallback; rename to `legacy_proxy_fetcher.py` |
| `src/scraping/parser/html_parser.py` | **MODIFY** | Add `Adaptor`-based parsing alongside selectolax |
| `src/services/firecrawl.py` | **DEPRECATE** | Mark deprecated; remove after benchmark validation |
| `src/services/scraper.py` | **MINOR MODIFY** | Update to leverage Scrapling `Adaptor` for `_find_main_content` |
| `src/services/crawler.py` | **MINOR MODIFY** | Use Scrapling fetcher in `_crawl_recursive` for stealth crawling |
| `pyproject.toml` | **MODIFY** | Add `scrapling` dependency |
| `requirements.txt` | **MODIFY** | Add `scrapling` |
| `tests/unit/scraping/` | **CREATE** | Unit tests for all three new fetcher classes |
| `benchmarks/scrapling_benchmark.py` | **CREATE** | Domain benchmark comparison script |

---

## 5. Technical Approach

### 5.1 Updated Tech Stack (Changes Only)

| Layer | Before | After | Rationale |
|-------|--------|-------|-----------|
| HTTP Fetching | httpx (manual headers) | Scrapling `Fetcher` (httpx under the hood + smart parsing) | Same perf, richer response objects with Adaptor integration |
| Browser Fetching | Raw Playwright | Scrapling `StealthyFetcher` (Playwright + stealth patches) | Anti-detection built in — no manual fingerprint management |
| Proxy Fetching | Raw Playwright + proxy flag | Scrapling `StealthyFetcher` + proxy config | Same proxy support, better stealth |
| HTML Parsing | selectolax only | selectolax + Scrapling `Adaptor` | Adaptor adds auto-matching selectors that survive site redesigns |
| Firecrawl | CLI/API dependency | **Removed** | This is the whole point |

### 5.2 New Fetcher Implementation Pseudocode

#### `ScraplingFetcher` (Tier 1 — Basic HTTP)

```python
# src/scraping/fetcher/scrapling_fetcher.py

import time
from scrapling import Fetcher
from src.config.constants import TIER_COSTS
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier

class ScraplingFetcher:
    """Tier 1: Fast HTTP fetcher using Scrapling's Fetcher."""

    def __init__(self):
        self.fetcher = Fetcher(auto_match=False)

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        options = options or FetchOptions()
        start = time.time()

        try:
            # Scrapling's Fetcher.get() returns an Adaptor-wrapped response
            response = self.fetcher.get(url, timeout=options.timeout / 1000)
            html = response.html_content  # or str(response)
            status_code = response.status
            blocked = status_code in (403, 429, 503) or len(html) < 200
            captcha = self._detect_captcha(html)
        except Exception:
            html, status_code, blocked, captcha = "", 0, True, False

        return FetchResult(
            url=url,
            status_code=status_code,
            html=html,
            headers={},
            tier_used=ScrapingTier.BASIC_HTTP,
            cost_usd=TIER_COSTS["basic_http"],
            duration_ms=int((time.time() - start) * 1000),
            blocked=blocked,
            captcha_detected=captcha,
        )

    def _detect_captcha(self, html: str) -> bool:
        signals = ["captcha", "challenge-form", "cf-browser-verification",
                   "recaptcha", "hcaptcha", "turnstile"]
        lower = html.lower()
        return any(s in lower for s in signals)
```

#### `ScraplingStealthFetcher` (Tier 2 — Headless Stealth)

```python
# src/scraping/fetcher/scrapling_stealth_fetcher.py

import time
from scrapling import StealthyFetcher
from src.config.constants import TIER_COSTS
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier

class ScraplingStealthFetcher:
    """Tier 2: Stealth headless browser fetcher using Scrapling."""

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        options = options or FetchOptions()
        start = time.time()

        try:
            fetcher = StealthyFetcher(auto_match=False)
            # StealthyFetcher handles Playwright stealth patches, 
            # realistic fingerprints, and evasion automatically
            response = fetcher.fetch(
                url,
                headless=True,
                network_idle=True,
                timeout=options.timeout,
            )
            html = response.html_content
            status_code = response.status
            blocked = status_code in (403, 429, 503) or len(html) < 200
            captcha = any(s in html.lower() for s in 
                         ["captcha", "challenge-form", "recaptcha", "hcaptcha"])
        except Exception:
            html, status_code, blocked, captcha = "", 0, True, False

        return FetchResult(
            url=url,
            status_code=status_code,
            html=html,
            headers={},
            tier_used=ScrapingTier.HEADLESS_BROWSER,
            cost_usd=TIER_COSTS["headless_browser"],
            duration_ms=int((time.time() - start) * 1000),
            blocked=blocked,
            captcha_detected=captcha,
        )
```

#### `ScraplingProxyFetcher` (Tier 3 — Stealth + Proxy)

```python
# src/scraping/fetcher/scrapling_proxy_fetcher.py

import time
from scrapling import StealthyFetcher
from src.config.constants import TIER_COSTS
from src.config.settings import get_settings
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier

class ScraplingProxyFetcher:
    """Tier 3: Stealth browser + residential proxy via Scrapling."""

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        options = options or FetchOptions()
        settings = get_settings()
        start = time.time()

        proxy_url = settings.brightdata_proxy_url or settings.smartproxy_url

        try:
            fetcher = StealthyFetcher(auto_match=False)
            response = fetcher.fetch(
                url,
                headless=True,
                network_idle=True,
                timeout=options.timeout,
                proxy={"server": proxy_url} if proxy_url else None,
            )
            html = response.html_content
            status_code = response.status
            blocked = status_code in (403, 429, 503) or len(html) < 200
            captcha = any(s in html.lower() for s in 
                         ["captcha", "challenge-form", "recaptcha"])
        except Exception:
            html, status_code, blocked, captcha = "", 0, True, False

        return FetchResult(
            url=url,
            status_code=status_code,
            html=html,
            headers={},
            tier_used=ScrapingTier.HEADLESS_PROXY,
            cost_usd=TIER_COSTS["headless_proxy"],
            duration_ms=int((time.time() - start) * 1000),
            blocked=blocked,
            captcha_detected=captcha,
        )
```

#### Updated Factory

```python
# src/scraping/fetcher/factory.py (updated)

from src.models.scraping import ScrapingTier
from src.scraping.fetcher.scrapling_fetcher import ScraplingFetcher
from src.scraping.fetcher.scrapling_stealth_fetcher import ScraplingStealthFetcher
from src.scraping.fetcher.scrapling_proxy_fetcher import ScraplingProxyFetcher

_FETCHERS = {
    ScrapingTier.BASIC_HTTP: ScraplingFetcher,
    ScrapingTier.HEADLESS_BROWSER: ScraplingStealthFetcher,
    ScrapingTier.HEADLESS_PROXY: ScraplingProxyFetcher,
}

def create_fetcher(tier: ScrapingTier):
    """Create a fetcher instance for the given tier."""
    fetcher_class = _FETCHERS.get(tier, ScraplingFetcher)
    return fetcher_class()
```

### 5.3 Technical Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Scrapling's sync Playwright calls block the async arq worker event loop | Medium | High | Wrap Scrapling fetch calls in `asyncio.to_thread()` or use Scrapling's async API if available; benchmark thread pool executor perf |
| Scrapling dependency adds heavy install footprint to Docker image | Low | Medium | Use `scrapling[all]` only in worker Dockerfile; API server doesn't need it |
| Scrapling API changes between versions | Low | Low | Pin version in pyproject.toml; abstract behind our FetchResult interface |
| Stealth patches detected by newer anti-bot systems | Medium | Medium | Scrapling actively maintained (1,134 commits); fallback to proxy tier; monitor blocked rate |
| `auto_match` feature causes unexpected selector behavior | Low | Low | Disable `auto_match` for MVP; enable in M1 after testing |

### 5.4 Async Compatibility Note

Scrapling's `Fetcher` uses `httpx` under the hood (sync by default) and `StealthyFetcher` uses Playwright synchronously. Since LakeStream's workers are async (arq), the safest pattern is:

```python
import asyncio

class ScraplingStealthFetcher:
    async def fetch(self, url, options=None):
        # Run sync Scrapling call in thread pool to avoid blocking event loop
        return await asyncio.to_thread(self._sync_fetch, url, options)

    def _sync_fetch(self, url, options):
        fetcher = StealthyFetcher(auto_match=False)
        return fetcher.fetch(url, headless=True, network_idle=True)
```

This is the key architectural decision. Test it in Week 1 and validate throughput under concurrent load.

---

## 6. Cost Estimate

### 6.1 Build Cost (MVP)

| Category | Estimate | Notes |
|----------|----------|-------|
| Engineering — fetcher integration | ~20-25 hours | 3 new fetcher classes + factory update |
| Engineering — parser upgrade | ~8-10 hours | Adaptor integration in html_parser, contact_parser |
| Engineering — benchmark + testing | ~10-12 hours | 50-domain benchmark + unit tests |
| Engineering — deploy + monitoring | ~5 hours | Railway staging deploy, log review |
| Infrastructure (additional) | $0/mo | Scrapling is OSS; no new infra needed |
| **Total MVP Build** | **~45-50 hours** | **~2 weeks at moderate pace** |

### 6.2 Ongoing Cost Savings

| Category | Before (Monthly) | After (Monthly) | Savings |
|----------|-------------------|------------------|---------|
| Firecrawl API | Variable (per-call) | $0 | 100% of Firecrawl spend |
| Proxy services | Same | Same | Unchanged (still use Bright Data for Tier 3) |
| Railway compute | Same | ~Same (+minor CPU for local Playwright) | Negligible |
| Maintenance burden | Firecrawl API monitoring + incident response | Scrapling version updates (quarterly) | Net reduction |

---

## 7. Success Metrics

| Metric | Type | Current Baseline | Target (MVP) | Target (M1) | Measurement |
|--------|------|------------------|-------------|-------------|-------------|
| Scrape success rate | North Star | ~85% | ≥90% | ≥93% | `SELECT count(*) WHERE blocked=false / total FROM fetch_results` |
| Firecrawl API cost | Financial | $X/mo | $0 | $0 | Invoice line item = zero |
| Avg page fetch latency | Leading | ~3-5s | ≤2.5s | ≤2s | p50 of `duration_ms` in FetchResult |
| Captcha/block rate | Guardrail | ~15-20% | ≤10% | ≤7% | `captcha_detected OR blocked` rate |
| Worker throughput | Leading | N jobs/hr | ≥1.2× current | ≥1.5× current | arq job completion rate per hour |
| Content extraction completeness | Quality | Manual spot-check | ≥95% field fill on contact pages | ≥97% | Automated field-presence check on scraped_data |

---

## 8. Competitive Positioning (Internal)

**Positioning Statement:**
For the Lake B2B data engineering team, LakeStream (post-Scrapling) is the fully self-hosted scraping platform that gives us Firecrawl-level stealth and extraction quality, unlike the current Firecrawl-dependent pipeline, because we own the entire stack and pay zero per-call API fees.

**Structural Advantage Over Firecrawl:**
- Full control over stealth behavior, retry logic, and extraction templates
- Domain-specific optimization (Lake B2B's contact/pricing/tech parsers run locally, not through a generic API)
- No vendor risk — Scrapling is BSD-3-Clause, 8K+ GitHub stars, actively maintained

**Why Scrapling Over Alternatives:**

| Library | Stars | Stealth | Async | Selectors | Auto-Match | MCP | License |
|---------|-------|---------|-------|-----------|------------|-----|---------|
| **Scrapling** | **8K+** | **Built-in** | **Partial** | **CSS+XPath+Text+Regex** | **Yes** | **Yes** | **BSD-3** |
| Crawl4AI | ~5K | Basic | Yes | CSS | No | No | Apache-2.0 |
| Playwright (raw) | 70K+ | None (DIY) | Yes | CSS+XPath | No | No | Apache-2.0 |
| Selenium | 30K+ | None (DIY) | No | CSS+XPath | No | No | Apache-2.0 |

---

## 9. Timeline & Milestones

### MVP → Deploy (3 Weeks)

| Week | Focus | Deliverables | Gate Criteria |
|------|-------|-------------|---------------|
| **1** | Fetcher integration | `ScraplingFetcher`, `ScraplingStealthFetcher`, `ScraplingProxyFetcher` classes created and unit tested; factory updated; `asyncio.to_thread` wrapper validated | All 3 fetchers pass unit tests; no event loop blocking under concurrent load |
| **2** | Parser + benchmark | Adaptor-based parsing in `html_parser.py`; 50-domain benchmark script; FirecrawlService deprecated | Benchmark shows ≥90% success rate; extraction quality matches or exceeds baseline |
| **3** | Harden + deploy | Edge case handling (timeouts, memory, large pages); Railway staging deploy; legacy fetchers renamed; monitoring dashboards updated | Staging runs 24hr soak test with no crashes; blocked rate ≤10% |

### Post-MVP Milestones

| Milestone | Target | Key Features | Gate to Next |
|-----------|--------|--------------|--------------|
| **M1: Tuning** | Week 5 | Enable Scrapling `auto_match` for resilient selectors; per-domain Scrapling profile configs; Camoufox evaluation for hardest-to-scrape domains | Success rate ≥93%; zero regressions from auto_match |
| **M2: Firecrawl Full Removal** | Week 6 | Delete `firecrawl.py`, remove any remaining Firecrawl references from codebase and env vars; update CLAUDE.md and docs | Clean `grep -r "firecrawl"` returns zero results |
| **M3: MCP + AI Agent** | Week 8-10 | Evaluate Scrapling's MCP server for AI-agent-driven scraping workflows; potential integration with LakeCurrent intent pipeline | POC demo of AI agent triggering scrape jobs via MCP |

---

## 10. Pivot Strategy

**Pivot Triggers:**
- Scrapling benchmark success rate <80% on the 50-domain test set
- `asyncio.to_thread` wrapper introduces >2× latency overhead vs. raw Playwright
- Scrapling maintainer goes inactive (no commits for 60+ days during integration period)

**Pivot Options:**

| Trigger | Pivot Direction | What Changes | What Stays |
|---------|----------------|-------------|------------|
| Low success rate | Layer Bright Data SDK as stealth tier instead of Scrapling `StealthyFetcher` | Tier 2 fetcher implementation | Factory pattern, Tier 1 (Scrapling HTTP), all parsers |
| Async perf issues | Use Scrapling for sync-only batch jobs; keep raw async Playwright for real-time jobs | Dual fetcher paths by job type | Factory pattern, escalation service, cost tracking |
| Project abandonment | Fork Scrapling or switch to Crawl4AI | Fetcher implementations | Everything above the fetcher layer |

**Kill Criteria:**
If after 4 weeks of engineering effort the pipeline shows *worse* performance than the current Firecrawl + Playwright hybrid, revert to the existing codebase and evaluate a paid managed alternative (ScrapingBee, Apify) with a cost ceiling below Firecrawl's.

---

## Appendix

### A. Scrapling Key APIs for Integration

```python
# Basic HTTP fetching (Tier 1 replacement)
from scrapling import Fetcher
fetcher = Fetcher()
page = fetcher.get("https://example.com")
page.css("h1")           # CSS selectors
page.xpath("//h1")       # XPath
page.find_by_text("Contact")  # Text matching

# Stealth browser fetching (Tier 2 replacement)
from scrapling import StealthyFetcher
fetcher = StealthyFetcher()
page = fetcher.fetch("https://protected-site.com", headless=True)
page.css(".contact-info")

# Response is an Adaptor object — replaces selectolax for parsing
page.css_first("title").text()
page.css(".email a::attr(href)")

# Proxy support (Tier 3 replacement)
page = fetcher.fetch(url, proxy={"server": "http://proxy:port"})
```

### B. Dependency Addition

```toml
# pyproject.toml — add to dependencies
"scrapling>=0.2.0",
```

```txt
# requirements.txt — add
scrapling>=0.2.0
```

```dockerfile
# Dockerfile — may need system deps for Playwright/Camoufox
RUN playwright install chromium --with-deps
```

### C. Files to Create (Checklist)

- [ ] `src/scraping/fetcher/scrapling_fetcher.py`
- [ ] `src/scraping/fetcher/scrapling_stealth_fetcher.py`
- [ ] `src/scraping/fetcher/scrapling_proxy_fetcher.py`
- [ ] `benchmarks/scrapling_benchmark.py`
- [ ] `tests/unit/scraping/test_scrapling_fetcher.py`
- [ ] `tests/unit/scraping/test_scrapling_stealth_fetcher.py`
- [ ] `tests/integration/test_scrapling_escalation.py`

### D. Files to Modify (Checklist)

- [ ] `src/scraping/fetcher/factory.py` — swap fetcher registry
- [ ] `src/scraping/parser/html_parser.py` — add Adaptor path
- [ ] `src/services/scraper.py` — use Adaptor in `_find_main_content`
- [ ] `src/services/crawler.py` — use ScraplingFetcher in `_crawl_recursive`
- [ ] `pyproject.toml` — add scrapling dependency
- [ ] `requirements.txt` — add scrapling dependency
- [ ] `Dockerfile` — ensure Playwright/Chromium deps installed

### E. Files to Deprecate/Remove (M2)

- [ ] `src/services/firecrawl.py` — delete after M1 validation
- [ ] `src/scraping/fetcher/http_fetcher.py` → rename to `legacy_http_fetcher.py`
- [ ] `src/scraping/fetcher/browser_fetcher.py` → rename to `legacy_browser_fetcher.py`
- [ ] `src/scraping/fetcher/proxy_fetcher.py` → rename to `legacy_proxy_fetcher.py`

---

*Generated via CHAMP Framework — Champions Group Engineering*
