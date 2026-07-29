# LakeStream v2 vs Firecrawl, Apify & the Open-Source Field

**Date:** 2026-07-10 · **Supersedes:** `FIRECRAWL_COMPARISON.md` (see [What changed](#3-what-changed-since-the-last-comparison))

All competitor features and prices were fetched fresh from live documentation and pricing pages on 2026-07-10 (source URLs in the [Appendix](#13-appendix)). All LakeStream claims are traced to source files in this repo. The LakeStream output sample in §6 was captured live from this codebase on the same date.

---

## 1. TL;DR

| | Positioning | One-line verdict vs LakeStream |
|---|---|---|
| **LakeStream v2** | Self-hosted, cost-tiered, domain-wide **B2B extraction platform** — crawl a domain once, extract typed records (articles, contacts, tech stack, pricing) into Postgres, export CSV/JSON | The only one of the four purpose-built for lead-gen data shapes, with zero per-page fees |
| **Firecrawl** | Managed **page → LLM-ready data API** (markdown, JSON extraction, search, agents) | Best markdown fidelity and the richest single-page feature set (screenshots, actions, caching, change tracking, browser sandbox); pay per page, anti-bot is cloud-only |
| **Apify** | **Marketplace platform**: 50,875 actors (store count, 2026-07-10) + scheduling, storage, proxies, webhooks | Buys you breadth (any site someone has built an actor for) at marketplace prices and variable third-party quality |
| **Open-source (Crawl4AI / Jina Reader / Scrapy)** | Free building blocks | Crawl4AI is the closest free analog to LakeStream's fetch layer; none ship typed B2B extraction or a job platform |

**Bottom line:** LakeStream competes head-on with Firecrawl's `/crawl`+`/extract` and Apify's Website Content Crawler for *domain-wide structured extraction*, and wins on cost and data shape for B2B pipelines. It intentionally does not compete on single-page premium features (screenshots, browser actions, change tracking) or on marketplace breadth.

---

## 2. The two products inside LakeStream

Comparisons only make sense per-surface, because LakeStream has two:

1. **Single-page scraper** — `ScraperService.scrape(url, tier, only_main_content)` (`src/services/scraper.py`), returning `{markdown, metadata, success, tier_used, status_code}`. This is the direct Firecrawl `/scrape` analog.
2. **Domain job pipeline** — `POST /scrape/execute` → arq queue → `DomainMapper` (sitemap-first + BFS crawl, `src/services/crawler.py`) → `ContentWorker` ("fetch once, extract everything", `src/workers/content_worker.py`) → Postgres → CSV/JSON export (`src/api/routes/exports.py`). This is the analog of Firecrawl `/crawl`+`/extract` and of an Apify crawler actor + dataset.

---

## 3. What changed since the last comparison

The previous `FIRECRAWL_COMPARISON.md` predated several v2 features and understated the product. Corrections:

| Old claim | Reality in v2 | Source |
|---|---|---|
| "LakeStream provides raw HTML — you'd need to post-process to get markdown" | Native markdown scraper exists: main-content detection (`main`/`article`/`[role=main]`/…), nav/footer/ad stripping, `markdownify` conversion | `src/services/scraper.py` |
| "LakeStream uses CSS selectors… less flexible" (no LLM extraction) | Full LLM extraction layer: per-data-type prompts + JSON schemas via OpenRouter, modes `css`/`ai`/`auto`/`prompt` at `/scrape/extract`, and `llm_mode` `off`/`fallback`/`only` on jobs | `src/services/llm_extractor.py`, `src/api/routes/scrape.py` |
| Tiers "HTTP → Playwright → Proxy", basic tier $0.0001 | Tiers renamed and repriced: `lightpanda` $0.001 → `playwright` $0.003 → `playwright_proxy` $0.0035 | `src/config/constants.py:2-6` |
| "6 data types" | 9: `blog_url`, `article`, `resource`, `contact`, `tech_stack`, `pricing`, `page`, `document`, `extracted` | `src/models/scraped_data.py:8-17` |
| Firecrawl `/v0/scrape`, "instant results" | Firecrawl is on **v2** (`api.firecrawl.dev/v2`); its `/crawl` and `/extract` are **async job-based** (submit → poll), same model as LakeStream jobs | docs.firecrawl.dev, 2026-07-10 |
| Firecrawl "~$0.01/page" | Credit-based: $0.60–$3.20 per 1,000 basic pages depending on plan; 5× that for stealth/JSON modes (see §8) | firecrawl.dev/pricing, 2026-07-10 |

---

## 4. Functionality matrix

Legend: ✅ built-in · 🟡 partial/limited · ❌ not available · 💰 paid add-on

| Capability | LakeStream v2 | Firecrawl | Apify (WCC¹ + platform) | Crawl4AI | Jina Reader | Scrapy |
|---|---|---|---|---|---|---|
| Page → markdown | ✅ `ScraperService` | ✅ (default format) | ✅ (`markdown` field) | ✅ (core focus) | ✅ (core focus) | ❌ (DIY) |
| Main-content extraction | ✅ selector list + noise stripping | ✅ `onlyMainContent` + LLM clean | ✅ `htmlTransformer` (readability et al.) | ✅ BM25 filtering | ✅ | ❌ |
| URL discovery / site map | ✅ `DomainMapper` (sitemaps incl. indexes + BFS + Playwright fallback) | ✅ `/map` (up to 100k links) | 🟡 (crawler discovers as it goes) | ✅ `AsyncUrlSeeder` (sitemap + CommonCrawl) | ❌ | 🟡 (DIY spiders) |
| Full-site crawl | ✅ job pipeline, ≤500 pages/job | ✅ `/crawl`, default limit 10,000 | ✅ `maxCrawlPages` default 9,999,999 | ✅ BFS/DFS/best-first | ❌ (single URL) | ✅ (unbounded, DIY) |
| Schema/LLM structured extraction | ✅ CSS + LLM (`css`/`ai`/`auto`/`prompt`) | ✅ `json` format + `/extract` (multi-URL, glob) | 🟡 per-actor | ✅ CSS + LLM strategies | 🟡 JSON output mode | ❌ (DIY pipelines) |
| **Typed B2B outputs** (contacts, tech stack, pricing, articles) | ✅ 9 typed models out of the box | ❌ (bring your own schema) | 🟡 via separate lead-gen actors (quality varies, rated 3.3–3.6/5) | ❌ | ❌ | ❌ |
| Fetch-once-extract-all economics | ✅ one fetch feeds all extractors | ❌ (each format/mode costs credits per page) | 🟡 per-actor | 🟡 | ❌ | ✅ (DIY) |
| Anti-bot / escalation | ✅ 3-tier auto-escalation + CAPTCHA detection + geo proxy (BYO) | ✅ `proxy: basic/enhanced/auto` (cloud-only Fire-engine) | ✅ proxy products + per-actor stealth | 🟡 proxy escalation | 🟡 managed | ❌ (DIY middleware) |
| Per-domain adaptive rate limiting | ✅ exponential backoff, domain memory | 🟡 `delay`/`maxConcurrency` params | ✅ autoscaling politeness | 🟡 | n/a | ✅ AutoThrottle |
| Caching | ❌ | ✅ `maxAge` (2-day default), Lockdown mode | 🟡 per-actor | ✅ | ✅ | 🟡 HTTP cache middleware |
| Screenshots | ❌ | ✅ (`screenshot` format, full-page) | ✅ (WCC `screenshotUrl`, browser actors) | ✅ | ❌ | ❌ |
| Browser actions / live sessions | ✅ AI browser agent (`/scrape/browse`, browser-use) | ✅ `actions` + `/interact` browser sandbox (cloud-only) | ✅ (Playwright actors) | ✅ (JS execution) | ❌ | ❌ |
| PDF / documents | ✅ `/scrape/pdf` (tables, text) | ✅ `parsers: pdf`, `/parse` endpoint (Rust engine) | 🟡 per-actor | 🟡 | ✅ native PDF | ❌ |
| YouTube transcripts | ✅ `/scrape/youtube-transcript` | ✅ (since v2.3.0) | ✅ (actors) | ❌ | ❌ | ❌ |
| Authenticated scraping (session/cookies) | ✅ Chrome extension → `/scrape/session-cookies`, Redis session persistence | ✅ `profile` persistent sessions (cloud) | ✅ (per-actor cookie inputs) | ✅ storage state | ❌ | 🟡 DIY |
| LinkedIn / Apollo specializations | ✅ built-in (`/scrape/linkedin`, `/scrape/apollo`) | ❌ | ✅ third-party actors ($4–$10/1k profiles) | ❌ | ❌ | ❌ |
| Search → scrape | 🟡 `/api/discover/search` (LakeCurrent) | ✅ `/search` (web/news/images) | ✅ (actors) | ❌ | ✅ `s.jina.ai` | ❌ |
| Change tracking / monitoring | ❌ | ✅ `changeTracking` format + `/monitor` endpoint | 🟡 schedules + diff DIY | ❌ | ❌ | ❌ |
| Exports | ✅ CSV + JSON bulk per job | 🟡 API/webhooks (24h retention) | ✅ dataset → JSON/CSV/Excel/RSS | 🟡 (library) | ❌ | ✅ feeds (JSON/CSV/S3) |
| Streaming progress | ✅ SSE (`/scrape/stream/{id}`, Postgres LISTEN/NOTIFY) | ✅ webhooks (crawl.started/page/completed) | ✅ webhooks | n/a | n/a | n/a |
| MCP / AI-agent integration | ✅ `src/mcp_server.py` | ✅ official MCP server (OAuth) | ✅ mcp.apify.com + local server | ✅ | 🟡 | ❌ |
| Self-host | ✅ (it *is* self-hosted: Docker/Railway) | 🟡 AGPL core; anti-bot engine, `/agent`, `/interact` cloud-only | ❌ (platform) / 🟡 Crawlee OSS | ✅ Apache-2.0 | ✅ Apache-2.0 (Docker image)² | ✅ BSD |
| Scale ceiling per crawl | 🟡 **500 pages/job** (`max_pages`, `src/models/job.py:24`) | 10,000 default limit, credits are the real cap | effectively unbounded | unbounded | n/a | unbounded |
| Job queue / retries / heartbeats | ✅ arq + Redis, 3 tries, heartbeats, cooperative cancel | ✅ managed | ✅ managed | ❌ (library) | n/a | 🟡 (DIY/Scrapyd) |

¹ WCC = `apify/website-content-crawler`, Apify's flagship crawling actor (139k users).
² Jina Reader's OSS image excludes the ReaderLM-v2 model (CC-BY-NC, non-commercial).

---

## 5. Endpoint / concept mapping

| Firecrawl (v2) | LakeStream v2 | Apify equivalent |
|---|---|---|
| `POST /v2/scrape` (sync) | `ScraperService.scrape()` (library) — no public sync HTTP endpoint yet | Single-URL actor run |
| `POST /v2/map` | `DomainMapper.map_domain()` / `POST /api/discover/search` | (crawler discovers inline) |
| `POST /v2/crawl` → poll `GET /v2/crawl/{id}` | `POST /scrape/execute` → `GET /scrape/status/{id}` / SSE `GET /scrape/stream/{id}` | Start actor run → poll run → read dataset |
| `POST /v2/extract` (schema/prompt over URLs) | `extraction_schema` + `extraction_mode` on jobs; `POST /scrape/extract` for one URL | Per-actor output schema |
| `POST /v2/agent` (autonomous, no URLs) | `POST /scrape/browse` (browser-use agent) | AI agent actors |
| `/v2/parse` (PDF) | `POST /scrape/pdf` | PDF actors |
| Firecrawl MCP server | `src/mcp_server.py` | mcp.apify.com |
| — | `POST /scrape/linkedin`, `POST /scrape/apollo`, `POST /scrape/youtube-transcript`, `POST /scrape/session-cookies` | Apify Store actors (third-party) |

Notable API-ergonomics difference: Firecrawl's `/scrape` is synchronous (one call, one response). LakeStream's markdown scraper is currently library-only; exposing it as a sync endpoint would close the "quick single page" gap (see §12).

---

## 6. Output side-by-side (the heart of it)

### 6a. LakeStream v2 single-page output — **captured live from this codebase, 2026-07-10**

`await ScraperService().scrape("https://www.python.org/about/")` — playwright tier, HTTP 200, 4.1s:

```json
{
  "markdown": "## Getting Started\n\nPython can be easy to pick up whether you're a first time programmer or you're experienced with other languages. …",
  "metadata": {
    "url": "https://www.python.org/about/",
    "title": "About Python™ | Python.org",
    "description": "The official home of the Python Programming Language",
    "og_title": "Welcome to Python.org",
    "og_description": "The official home of the Python Programming Language",
    "og_image": "https://www.python.org/static/opengraph-icon-200x200.png",
    "canonical": "",
    "author": ""
  },
  "success": true,
  "tier_used": "playwright",
  "status_code": 200
}
```

The markdown itself (first lines, truncated):

```markdown
## Getting Started

Python can be easy to pick up whether you're a first time programmer or
you're experienced with other languages. The following pages are a useful
first step to get on your way writing programs with Python!

- [Beginner's Guide, Programmers](https://wiki.python.org/moin/BeginnersGuide/Programmers)
- [Beginner's Guide, Non-Programmers](https://wiki.python.org/moin/BeginnersGuide/NonProgrammers)

## Friendly & Easy to Learn

The community hosts conferences and meetups, collaborates on code,
and much more. Python's documentation will help you along the way…
… (truncated)
```

Navigation, footer, and sidebar were stripped by the main-content detector — output quality is directly comparable to Firecrawl's for standard pages. (Capture methodology and sandbox caveats: [Appendix](#13-appendix).)

### 6b. Firecrawl v2 `/scrape` response (from docs.firecrawl.dev, 2026-07-10)

```json
{
  "success": true,
  "data": {
    "markdown": "Launch Week I is here! [See our Day 2 Release 🚀](https://www.firecrawl.dev/blog/…)…",
    "html": "<!DOCTYPE html><html lang=\"en\" class=\"light\" …",
    "metadata": {
      "title": "Home - Firecrawl",
      "description": "Firecrawl crawls and converts any website into clean markdown.",
      "language": "en",
      "keywords": "Firecrawl,Markdown,Data,Mendable,Langchain",
      "robots": "follow, index",
      "ogTitle": "Firecrawl",
      "ogDescription": "Turn any website into LLM-ready data.",
      "ogUrl": "https://www.firecrawl.dev/",
      "ogImage": "https://www.firecrawl.dev/og.png?123",
      "ogSiteName": "Firecrawl",
      "sourceURL": "https://firecrawl.dev",
      "statusCode": 200,
      "contentType": "text/html"
    }
  }
}
```

Near-identical shape to 6a. Differences: Firecrawl can return many more formats in the same response (`html`, `rawHtml`, `links`, `screenshot`, `summary`, `json`, `changeTracking`, …) and slightly richer metadata (`language`, `keywords`, `robots`); LakeStream returns `tier_used` (cost transparency Firecrawl doesn't expose).

### 6c. Apify Website Content Crawler dataset item (from apify.com actor README, 2026-07-10)

```json
{
  "url": "https://docs.apify.com/academy/web-scraping-for-beginners",
  "crawl": {
    "loadedUrl": "https://docs.apify.com/academy/web-scraping-for-beginners",
    "loadedTime": "2023-04-05T16:26:51.030Z",
    "referrerUrl": "https://docs.apify.com/academy",
    "depth": 0
  },
  "metadata": {
    "canonicalUrl": "https://docs.apify.com/academy/web-scraping-for-beginners",
    "title": "Web scraping for beginners | Apify Documentation",
    "description": "Learn how to develop web scrapers with this comprehensive and practical course.…",
    "author": null,
    "keywords": null,
    "languageCode": "en"
  },
  "screenshotUrl": null,
  "text": "Skip to main content\nOn this page\nWeb scraping for beginners\n…",
  "html": null,
  "markdown": "  Web scraping for beginners | Apify Documentation …"
}
```

Same family of shape again — per-page markdown/text + metadata, plus crawl provenance (`depth`, `referrerUrl`).

### 6d. Where LakeStream's output diverges: typed records

None of the above competitors produce this without you writing schemas or buying separate actors. LakeStream's job pipeline emits **typed rows** (`ScrapedData`, `src/models/scraped_data.py`) — the same page fetch can yield an `article`, a `contact`, a `tech_stack`, and a `pricing` record:

```json
{
  "job_id": "35a93ddf-c541-42de-a43a-0de7f8cec347",
  "domain": "blog.hubspot.com",
  "exported_at": "2026-03-12T17:05:00Z",
  "total_records": 148,
  "data": [
    {
      "id": "12820439-4f08-4ab7-90af-a1c049c0be4a",
      "domain": "blog.hubspot.com",
      "data_type": "article",
      "url": "https://blog.hubspot.com/blog/…",
      "title": "Why List Segmentation Matters in Email Marketing",
      "published_date": null,
      "scraped_at": "2026-03-12T17:01:45.894500+00:00",
      "metadata": {
        "author": "Pamela Vaughan",
        "content": "Full article text (1,673 words)…",
        "excerpt": "Learn why marketers must segment…",
        "word_count": 1673,
        "categories": ["Marketing", "Email"]
      }
    }
  ]
}
```
*(Historical export sample from a March 2026 run, retained from the previous comparison doc.)*

Per-type metadata models (`src/models/scraped_data.py`):

| `data_type` | Fields |
|---|---|
| `article` | author, categories[], word_count, excerpt, content |
| `contact` | first_name, last_name, job_title, email, phone, linkedin_url, source |
| `tech_stack` | platform, js_libraries[], analytics[], marketing_tools[], frameworks[] |
| `resource` | resource_type, description, gated, download_url |
| `pricing` | plan_name, price, billing_cycle, features[], has_free_trial, cta_text |
| `blog_url` | blog_landing_url, article_urls[], total_articles |
| `document` | source_type, page_count, author, tables, word_count, text_content |
| `page` / `extracted` | raw page record / custom-schema results |

To get the equivalent from Firecrawl you'd define JSON schemas per type and pay 5 credits/page per extraction pass; from Apify you'd chain WCC with separate contact/lead actors of varying quality.

---

## 7. Anti-bot & fetching architecture

**LakeStream** (`src/services/escalation.py`, `src/scraping/fetcher/`):
- Three tiers with per-request cost accounting: `lightpanda` $0.001 (light browser, URL discovery) → `playwright` $0.003 (full Chromium, Redis-backed session persistence, networkidle for SPAs) → `playwright_proxy` $0.0035 (residential/geo proxy via BrightData/Smartproxy env config; `region`: us/eu/uk/de/asia/in/au).
- Escalation triggers: HTTP 403/429/503, suspiciously small HTML, CAPTCHA detection (`captcha_detector.py`). 429/503 wait-then-retry (120s/600s) before escalating; CAPTCHA escalates immediately.
- **Domain memory**: last successful tier per domain stored in Postgres and used as the starting tier next time.
- Per-domain adaptive rate limiter (`rate_limiter.py`): default full-throttle, exponential backoff ×2 to 30s on 429/503, −10% decay on success; LinkedIn hard-coded 3s floor.
- Authenticated scraping: Chrome extension captures session cookies → `AuthenticatedSessionManager`; LinkedIn session health tracking preemptively shifts aging sessions to proxy tier.

**Firecrawl**: `proxy: basic | enhanced | auto` (auto = try basic, retry enhanced; enhanced = 5 credits/page). The anti-bot capability lives in the proprietary cloud-only "Fire-engine" — **self-hosted Firecrawl does not get it**. No explicit CAPTCHA-solving claims in current docs. Geo via `location` param.

**Apify**: datacenter (per-IP) and residential ($7–8/GB) proxy products, per-actor stealth (WCC uses headless Firefox + adaptive switching), platform-level anti-blocking guidance.

**Verdict**: LakeStream's design is *cost-first* (escalate only when blocked, remember what worked); Firecrawl/Apify are *managed-first* (their fleets and IP pools absorb the problem at higher unit prices). For heavily defended targets, managed fleets will win; LakeStream requires you to bring proxy credentials.

---

## 8. Cost per 1,000 pages (as of 2026-07-10)

**Important caveat:** LakeStream numbers are *internal infra cost estimates* (`TIER_COSTS`, `src/config/constants.py`) and exclude hosting (~$20–50/mo for API+worker+Postgres+Redis on Railway-class infra) and proxy bandwidth. Competitor numbers are list prices.

| | Basic page | JS-heavy page | Blocked/stealth page |
|---|---|---|---|
| **LakeStream** | ~$1.00 (lightpanda) | ~$3.00 (playwright) | ~$3.50 (playwright_proxy) + BYO proxy GB |
| **Firecrawl** Hobby ($16/mo yearly) | $3.20 | $3.20 | $16.00 (enhanced = 5 cr) |
| **Firecrawl** Standard ($83/mo yearly) | $0.83 | $0.83 | $4.15 |
| **Firecrawl** Scale ($599/mo yearly) | $0.60 | $0.60 | $3.00 |
| **Apify** WCC | ~$0.20 (raw HTTP) | $0.50–$5.00 (browser) | + residential proxy $7–8/GB |
| **Crawl4AI / Scrapy** | compute only | compute only | + BYO proxies |
| **Jina Reader** | ~$0.02–0.05/1M output tokens (≈ pennies; third-party pricing, unverified) | same | n/a |

Reading this honestly:
- At **small volume** (≤5k pages/mo), Firecrawl Hobby ($16–19/mo) or Apify's raw-HTTP mode is cheaper than running LakeStream's infra — you're paying LakeStream's hosting floor regardless.
- At **B2B-pipeline volume** (50k–500k pages/mo) with mostly-friendly targets, LakeStream's marginal cost stays flat while Firecrawl Standard/Growth runs $83–$399/mo + overage, and JSON-mode extraction (which is what B2B pipelines actually need) multiplies Firecrawl's cost ×5 per page. This is LakeStream's economic sweet spot — plus the "fetch once, extract everything" design means six data types cost one fetch.
- Firecrawl's *unified* markdown+JSON pricing on already-scraped pages, `maxAge` caching (free re-reads within 2 days), and Lockdown mode have no LakeStream equivalent — repeat-scrape workloads narrow the gap.

Also relevant: Firecrawl plan concurrency is 2–150+ browsers and 10–7,500 scrapes/min; LakeStream's crawler runs `max_concurrent=15` / 6 per domain (`src/services/crawler.py`) per worker, scaling horizontally with workers you operate.

---

## 9. Where the competitors win (honest list)

**Firecrawl**
- Markdown edge-case fidelity and battle-testing across millions of pages; `summary`, `question/answer`, `highlights` formats (claimed up to 100× fewer tokens).
- Screenshots, browser `actions`, and the `/interact` live browser sandbox.
- Caching (`maxAge`), change tracking, and the new `/monitor` recurring change detection — LakeStream has none of this.
- `/search` (SERP + scrape), `/agent` autonomous gathering (Spark models), Research Index.
- No 500-page cap: crawl `limit` defaults to 10,000.
- Managed anti-bot at fleet scale; zero-data-retention and SSO for enterprise.
- Ecosystem: 8 official SDK languages, MCP with OAuth, LangChain/LlamaIndex integrations.

**Apify**
- 50,875 actors — someone has already built the scraper for almost any target, including ToS-gray-area ones (LinkedIn profiles at $4–10/1k) that an internal tool must build and maintain itself.
- Full platform: schedules, datasets, key-value stores, request queues, webhooks, 50+ integrations, autoscaling.
- Pay-per-result pricing on many actors = zero infra risk for spiky workloads.

**Both**: instant onboarding (API key vs deploying Postgres/Redis/workers), and their pages-per-crawl ceilings dwarf LakeStream's `max_pages ≤ 500` (`src/models/job.py:24`).

**LakeStream gaps called out plainly:** no screenshots; no caching/change tracking; no sync HTTP scrape endpoint (library-only); 500-page job cap; lightpanda tier needs an external `lightpanda_ws_url` service or the chain silently starts at playwright; BYO proxy; benchmark harness only just un-broken (this PR); data-quality of LLM extraction depends on your OpenRouter model choice.

---

## 10. Where LakeStream wins

1. **Typed B2B extraction out of the box** — contacts, tech stack, pricing, articles, resources as first-class models (§6d). Competitors give you markdown and a schema box.
2. **Fetch-once-extract-everything** — one page fetch feeds all extractors (`ContentWorker`); Firecrawl bills per format/mode per page.
3. **Cost-tier escalation with domain memory** — pay browser/proxy prices only for pages that need them, and remember per domain.
4. **Data ownership & flat economics** — everything lands in your Postgres; no per-page vendor fees, no 24h result expiry (Firecrawl crawl data expires; Apify datasets have plan-based retention).
5. **B2B specializations** — LinkedIn Sales Navigator + Apollo scrapers, session-cookie capture extension, YouTube transcripts, PDF tables — integrated with the same job/export pipeline instead of stitched-together actors.
6. **Pipeline-native UX** — SSE progress streaming, CSV exports shaped for lead-gen ingestion, template registry (WordPress/HubSpot/Webflow/directory/generic), MCP server for agent access.
7. **Self-hosted with full capability** — unlike Firecrawl, where self-hosting excludes exactly the hard parts (anti-bot engine, agent, sandbox).

---

## 11. Open-source tier (build-vs-buy reference points)

**Crawl4AI** (Apache-2.0, 72.2k stars, v0.9.1 2026-07-08) — the closest free analog to LakeStream's fetch+markdown layer: async Playwright crawling, BM25 content filtering, CSS+LLM extraction strategies, BFS/DFS deep crawl, sitemap+CommonCrawl URL seeding, MCP support. What it isn't: a job platform. No queue, no typed B2B models, no exports, no multi-tenant API — you'd rebuild LakeStream's top half around it. Worth watching as a potential fetcher-layer replacement.

**Jina Reader** (Apache-2.0 OSS + hosted `r.jina.ai`) — URL→markdown as a GET prefix; 500 RPM free with key; native PDF; token-based pricing (~pennies per site, third-party figures). Great as a cheap markdown fallback tier; no crawling, no extraction, and the OSS image excludes the non-commercial ReaderLM-v2 model.

**Scrapy** (BSD, 63.1k stars, v2.17.0 2026-07-07) — the 15-year framework: unmatched middleware/pipeline architecture, AutoThrottle, feed exports. No JS rendering without scrapy-playwright, no LLM anything, everything is DIY. If LakeStream didn't exist, this is what you'd build it on; migrating to it now buys nothing.

---

## 12. Recommendations

**Positioning:** keep LakeStream as the system of record for domain-wide B2B extraction. Don't chase Firecrawl's premium single-page features; do close the cheap gaps:

1. **Expose `ScraperService` as `POST /scrape/page`** (sync, `{url, tier?, only_main_content?}` → §6a shape). It exists; it's just not routed. Closes the "quick single page" ergonomic gap with Firecrawl at zero architectural cost.
2. **Raise or tier the 500-page cap** (`src/models/job.py:24`) — Firecrawl defaults to 10k, Apify to unlimited; 500 undersells the pipeline for enterprise domains.
3. **Add `markdown` as an export/data option on jobs** (store `ScraperService` markdown alongside typed records when requested) — makes LakeStream output directly LLM/RAG-consumable like all three competitors.
4. **Consider a simple `maxAge`-style cache** (page hash + fetched_at already land in Postgres) — repeat-crawl economics is Firecrawl's quietest advantage.
5. **Screenshots** only if sales needs them: one `page.screenshot()` call in the playwright fetcher behind a flag.
6. Benchmark harness is fixed in this change (`benchmarks/lake_benchmark.py` was referencing pre-rename tier enums and crashed on import since the tier migration); re-baseline tier success rates when convenient.

**When to reach for a competitor anyway:** one-off scrapes of heavily defended consumer sites (Firecrawl enhanced proxy), targets with a mature Apify actor you'd otherwise reverse-engineer (Google Maps at $1.50/1k places), or change-monitoring workloads (Firecrawl `/monitor`).

---

## 13. Appendix

### Sample-capture methodology
- Captured 2026-07-10 from this repo (branch `claude/v2-scraper-comparison-qykhm6`) in a sandboxed Linux container: `ScraperService().scrape(url)` directly (no DB/Redis/queue running — the service tolerates their absence; `JWT_SECRET` env var required by `src/config/settings.py`).
- 5/5 test URLs succeeded on the `playwright` tier (example.com, books.toscrape.com, quotes.toscrape.com, python.org/about, blog.python.org), 2.5–4.6s per page including browser launch.
- Sandbox caveat: the container's TLS-intercepting egress proxy resets Chromium's TLS handshakes, so page/subresource fetches were transparently fulfilled through an httpx transport shim at the Playwright routing layer. Chromium rendering, LakeStream's tier selection, main-content extraction, metadata parsing, and markdown conversion all ran unmodified — output shape and content are authentic; timing includes shim overhead. Outside this sandbox no shim is needed.
- The §6d typed-export JSON is a historical sample from a March 2026 production run (retained from the prior doc), consistent with the current `ScrapedData` models.
- LLM extraction modes were not exercised live (no OpenRouter key in the sandbox); described from `src/services/llm_extractor.py`.

### Superseded / fixed artifacts
- `FIRECRAWL_COMPARISON.md` → tombstone pointing here (kept to preserve inbound links).
- `benchmarks/lake_benchmark.py` → updated to current `ScrapingTier` members (`LIGHTPANDA`/`PLAYWRIGHT`/`PLAYWRIGHT_PROXY`) and fixed a `captcha_count`→`captcha_detected` attribute bug; runnable again.
- `ChampionInternalScraperPRD.md ` (note trailing space in filename) — internal PRD positioning LakeStream against Apify; unchanged, useful context for §1.

### Sources (all accessed 2026-07-10)
**Firecrawl:** docs.firecrawl.dev (api-reference/introduction, endpoint/scrape, endpoint/crawl-post, features/scrape, features/crawl, features/extract, features/agent, features/stealth-mode, rate-limits, contributing/self-host, llms.txt) · firecrawl.dev/pricing · www.firecrawl.dev/changelog · github.com/firecrawl/firecrawl. Notes: monthly-billing prices ($19/$99/$399/$749) derived from the pricing page's own yearly-vs-monthly "Save $X" arithmetic; page shows yearly rates ($16/$83/$333/$599) by default.
**Apify:** apify.com/apify/website-content-crawler · apify.com/pricing · apify.com/store (actor count) · apify.com/harvestapi/linkedin-profile-scraper · apify.com/compass/crawler-google-places · apify.com/vdrmota/contact-info-scraper · docs.apify.com/platform (+integrations/mcp, actors/publishing/monetize). Notes: Apify's 80% developer revenue share and 2026 actor-rental retirement dates verified via search snippets of Apify docs, not re-fetched; dedicated Apollo scrapers found delisted from the Store as of this date.
**Open source:** github.com/unclecode/crawl4ai · jina.ai/reader · github.com/jina-ai/reader · scrapy.org · github.com/scrapy/scrapy. Note: Jina per-token pricing is third-party-reported only.
**LakeStream:** `src/services/scraper.py`, `src/services/crawler.py`, `src/services/escalation.py`, `src/services/rate_limiter.py`, `src/services/llm_extractor.py`, `src/workers/content_worker.py`, `src/models/job.py`, `src/models/scraped_data.py`, `src/models/scraping.py`, `src/config/constants.py`, `src/api/routes/scrape.py`, `src/api/routes/exports.py`, `src/mcp_server.py`.
