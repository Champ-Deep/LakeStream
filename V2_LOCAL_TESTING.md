# LakeStream v2 — Local Testing Guide

This branch (`claude/v2-scraper-comparison-qykhm6`) adds, behind feature flags:
markdown persistence + custom-schema fallback, content caching + change
monitoring, screenshots + scripted browser actions, experimental Go fetcher
sidecars, a sitemap→knowledge graph, and a Clerk auth migration. Everything is
additive; with default flags the app behaves like before.

## Run it (one link)

```bash
docker compose -f docker-compose.local.yml up --build
```

Brings up: postgres (:7432), redis (:7379), the API (**http://localhost:7100**),
the arq worker, and the two Go fetcher sidecars. Migrations run on API start.
Open **http://localhost:7100** — that is your local test link.

### Named public URL (default for shareable local dev)

`http://localhost:7100` only works on the machine running the stack. To test
from a link (phone, another laptop, a teammate) use a **named tunnel** — this is
our standard for local dev instead of passing bare localhost URLs around.

Zero extra tooling — add the `tunnel` profile:

```bash
TUNNEL_SUBDOMAIN=lakestream-dev \
  docker compose -f docker-compose.local.yml --profile tunnel up --build
# → https://lakestream-dev.loca.lt  (maps to the api on :7100)
```

Or, if the app is already running, from the host:

```bash
./scripts/dev-link.sh lakestream-dev        # https://lakestream-dev.loca.lt
TUNNEL=cloudflared ./scripts/dev-link.sh     # random *.trycloudflare.com, no interstitial
```

loca.lt shows a one-time interstitial on first visit; the password it asks for
is your machine's public IP (`curl https://loca.lt/mytunnelpassword`). For a
durable, always-on URL (not tied to your laptop being on), deploy to Railway —
see `DEPLOYMENT.md`.

## Feature flags (env, all set in docker-compose.local.yml)

| Flag | Default | Effect |
|------|---------|--------|
| `ENABLE_CONTENT_PERSISTENCE` | true | store markdown + raw HTML + hash per page |
| `ENABLE_SCRAPE_CACHE` | true | skip re-extraction of unchanged pages |
| `ENABLE_CHANGE_MONITORING` | true | record changes + fire tracked-domain webhooks |
| `ENABLE_SCREENSHOTS` | false | allow per-page screenshots (opt-in per job) |
| `ENABLE_GO_FETCHERS` | true | make the `go_http` / `go_browser` tiers available |
| `ENABLE_KNOWLEDGE_GRAPH` | true | capture crawl edges + serve the graph |
| `AUTH_PROVIDER` | legacy | `legacy` = password login; `clerk` = Clerk sign-in |

## Auth: legacy vs Clerk

- **Legacy (default):** log in at `/login` with `admin@lakeb2b.internal` /
  `LakeB2B_admin!` (or `ADMIN_EMAIL`/`ADMIN_PASSWORD`). Nothing else needed.
- **Clerk:** in `docker-compose.local.yml`, set `AUTH_PROVIDER: "clerk"` and fill
  `CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `CLERK_JWKS_URL`, `CLERK_ISSUER`,
  `CLERK_DOMAIN` from your Clerk **dev** instance. In the Clerk dashboard create
  two users and set one user's **public metadata** to `{"role": "super_admin"}`.
  `/login` then renders Clerk's embedded sign-in; users are provisioned locally
  on first sign-in. To migrate existing password users by email in a one-time
  pass, set `CLERK_LINK_BY_EMAIL: "true"` (leave off otherwise).

## Old-vs-new comparison checklist

Parity (should behave as before):
- [ ] Login / logout; session persists across requests.
- [ ] Create a scrape job (any tier) → same typed output shape in results.
- [ ] Scheduled scrapes + tracked-domain webhooks still fire.
- [ ] Chrome extension `X-API-Key` ingest to `/api/ingest`.
- [ ] User management + cross-user "god view" (super-admin only).

New in v2:
- [ ] **Bulk upload** (`/jobs/bulk`) now works for **any** logged-in user (was admin-only).
- [ ] `page_content` rows carry markdown + raw HTML + `content_hash` after a job.
- [ ] Re-scrape an unchanged page → worker logs `cache_hit_skip_extraction`.
- [ ] Change a page, re-scrape → a `content_changes` row + webhook; the domain
      detail page shows it under "Recent Content Changes".
- [ ] Custom schema: `POST /scrape/execute` with `extraction_schema` +
      `extraction_mode:"auto"` → `extracted` records; pages that match nothing
      still keep clean markdown.
- [ ] Screenshot: run a job with `capture_screenshot:true` → PNG served at
      `/api/screenshots/{job_id}/{name}`.
- [ ] Go tiers: `POST /scrape/execute` with `tier:"go_http"` or `"go_browser"`
      completes; `python -m benchmarks.lake_benchmark <url>` A/Bs Python vs Go.
- [ ] Knowledge graph: crawl a domain, open its detail page → interactive
      Cytoscape graph; `GET /api/graph/{domain}` returns nodes+edges JSON.

## v2.1 "Enrichment Edition" — new public-API surface

Four new endpoint families (all `X-API-Key`-authenticated; create a key in
Settings → API keys):

```bash
KEY="ls_..."   # your API key
B=http://localhost:7100

# 1. Sync single-URL scrape → markdown (the careers-page fetch for pipeline-v2)
curl -s $B/api/scrape/url -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"url":"https://stripe.com/careers"}'

# 2. Parse Bytes — PDF/DOCX/HTML/text → markdown (upload OR url)
curl -s $B/api/parse -H "X-API-Key: $KEY" -F file=@whitepaper.pdf
curl -s $B/api/parse -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/report.pdf"}'

# 3. Web search (LakeCurrent-backed), optional inline markdown scrape
curl -s $B/api/search -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"query":"series b fintech india", "scrape": true}'

# 4. Company enrichment (domain | email | company_name)
curl -s $B/api/enrich -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"domain":"stripe.com"}'

# 5. Tech stack detection (v2.1.1) — page-level signals via a scrape job
curl -s $B/api/scrape/execute -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"domain":"example.com","max_pages":1,"data_types":["tech_stack"]}'
# -> {"job_id":"..."}; poll /api/jobs/{job_id}, then read the tech_stack record's
# metadata: platform, js_libraries, analytics, marketing_tools, frameworks,
# cdn, widgets, web_servers, programming_languages, server_os.
# Domain-level facts (hosting, email hosting, CDN via DNS, SSL certificate) are
# on /api/enrich instead — see its response's web_hosting_provider,
# email_hosting_provider, cdn_providers, ssl_issuer/ssl_valid_to/ssl_protocol.

# 6. Technology lookup (v2.2) — domain in, full stack out (API)
curl -s $B/api/tech -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"domain":"wordpress.org"}'
# Bulk (up to 50 domains, resolved concurrently — this is the 100K-run path):
curl -s $B/api/tech/bulk -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"domains":["stripe.com","shopify.com"],"include_detections":false}'
# What catalog is loaded?
curl -s $B/api/tech/catalog -H "X-API-Key: $KEY"
# Frontend: http://localhost:7100/tech  (paste domains, get a results table)

# Usage + limits
curl -s $B/api/usage -H "X-API-Key: $KEY"
# Job-status alias used by the enrichment-pipeline playbook:
curl -s $B/api/jobs/<job_id> -H "X-API-Key: $KEY"
```

Inbound protection: every `/api/*` call is rate-limited per key
(`RATE_LIMIT_PER_MINUTE`, default 60/min → 429 + Retry-After); billable POSTs
(scrape/parse/search/enrich/discover) are metered 1 credit each into
`api_usage`. Hard credit enforcement is off by default
(`ENABLE_CREDIT_ENFORCEMENT=false` records only).

### Enrichment-pipeline (Lenovo/Harte Hanks) fit checklist

- [ ] `POST /api/scrape/execute` + poll `GET /api/jobs/{job_id}` → status +
      export links (the Intern Playbook's exact polling path now works).
- [ ] `POST /api/scrape/url` returns markdown for a single careers page
      (JS-rendered via Playwright tier escalation) — pipeline-v2's `fetch.py`
      hosted-mode path.
- [ ] `/api/search` answers the "DuckDuckGo blocks us" pivot in the runbook
      without a paid Serper/Brave key (needs LakeCurrent up).
- [ ] `/api/enrich` fills the firmographic passthrough columns (industry,
      NAICS/SIC, size) of the combined-file schema.
- [ ] The in-repo contract `ScraperService().scrape(url)` → `{"markdown": ...}`
      is unchanged (pipeline-v2 `fetch.py` imports it directly).

## Technology detection engine (v2.2) — catalog-scale + LLM judge

`POST /api/tech` (single), `POST /api/tech/bulk` (concurrent), and the
**`/tech` frontend page** all run the same engine. Full detail in
[`docs/TECH_CATALOG.md`](docs/TECH_CATALOG.md).

**Regex catalog extracts; the LLM only judges.** Detection is deterministic.
The judge (opt-in via `ENABLE_TECH_JUDGE=true` or `{"judge":true}`) reviews
only medium-confidence body matches and can *remove* a false positive — it can
never add a technology, never overrule a high-confidence structural match, and
never fail the pipeline.

**Bring your own catalog.** The built-in curated set (~130 signatures) always
loads. Point `TECH_CATALOG_PATH` at a Wappalyzer-format catalog to add
thousands more — it is loaded at runtime and deliberately not vendored into
this repo, because the maintained community forks are **GPL-3.0** and
LakeStream is distributed. `docs/TECH_CATALOG.md` explains the position;
`scripts/import_tech_catalog.py` validates a catalog and reports how many of
its entries rely on `js`/`dom` signals that need a browser and so will never
fire.

**Performance.** Precompiled catalog + targeted matching (script URLs, a named
header, a cookie, a meta tag — not the whole document) + a literal prefilter:
~24 ms/page, ≈0.7 h per 100K on one core. A naive full-HTML scan of the same
catalog is ~25 s/page (~690 h per 100K). Measure yours:
`python -m benchmarks.tech_engine_benchmark 100000`. At 100K, network fetch —
not detection — is the bottleneck.

## Tech stack detection (v2.1.1) — BuiltWith-comparison fields

Added to close the gap identified when comparing LakeStream's tech-stack output
against BuiltWith: OS, web hosting, email hosting, JS libraries, widgets, web
servers, analytics, frameworks, frontend/backend programming languages, SSL
certificate, and CDN.

**Split by where the fact actually lives** (this is deliberate, not an oversight):
- **Page-level** (varies per page, from HTML/headers/cookies of the scraped
  page): `platform`, `js_libraries`, `analytics`, `marketing_tools`,
  `frameworks`, `cdn`, `widgets`, `web_servers`, `programming_languages`,
  `server_os` — on `TechStackMetadata`, via `data_types:["tech_stack"]` jobs.
- **Domain-level** (doesn't vary per page, resolved once via DNS/TLS, cached):
  `web_hosting_provider`, `email_hosting_provider`, `cdn_providers`,
  `ssl_issuer`, `ssl_valid_from/to`, `ssl_days_until_expiry`, `ssl_protocol`,
  `ssl_san_domains` — on `CompanyProfile`, via `/api/enrich`.

**Detection method:** an original, hand-curated regex fingerprint database
(`src/data/tech_signatures.py`, ~130 signatures) matched against HTML body,
response headers (by name AND value — e.g. the bare presence of a `CF-RAY`
header signals Cloudflare even though its value is just a request ID), and
`Set-Cookie` cookie names (e.g. `PHPSESSID` → PHP, `ASP.NET_SessionId` →
ASP.NET). Hosting/email-hosting/CDN come from DNS (`src/services/dns_intel.py`
— NS/MX/CNAME records against a nameserver/MX heuristic table); the SSL
certificate comes from a real TLS handshake (`src/services/ssl_intel.py` —
stdlib `ssl`/`socket` + `cryptography` for the unverified-cert fallback).

**Known limitations, stated honestly:**
- The fingerprint set is hand-curated (~130 signatures across CMS, analytics,
  marketing, frameworks, CDN, JS libraries, widgets, web servers, programming
  languages) — not the thousands of entries a mature commercial database like
  BuiltWith's has built up over years. Long-tail/niche technologies will miss.
- Any substring-style fingerprint (ours or BuiltWith's) risks false positives
  when a page merely *mentions* a technology (e.g. a customer-logo strip) —
  we found and fixed one live during testing (a bare "shopify" mention
  misdetected a non-Shopify site); if you hit another, tighten that signature
  in `tech_signatures.py` to require a domain-qualified or header-based marker.
- `web_hosting_provider`/`cdn_providers` (DNS-based) only recognize the
  providers in the curated nameserver/CNAME tables — an unrecognized host
  returns `null`, not a wrong answer.
- **This sandbox's outbound TLS is intercepted** (same limitation as the
  Playwright/chromedp note below) — `/api/enrich`'s `ssl_issuer` will show the
  interception proxy's cert here, not the real one. Verified correct in
  isolation (real cert parsing logic tested directly); will show real
  certificate data in your local Docker environment, which has no such proxy.
- Response headers now flow through on **all** Playwright-based tiers (this
  branch fixes a bug where they were hardcoded to `{}`); the Go HTTP tier
  already captured them correctly.

## Notes / limitations observed while building

- In this cloud sandbox, headless-Chrome tiers (Playwright and `go_browser`)
  can't complete external HTTPS because the egress proxy resets Chromium's TLS;
  they work normally in your local Docker. The `go_http` tier and all
  DB/extraction logic were verified end-to-end.
- Rollback for auth: set `AUTH_PROVIDER=legacy` (default) — Clerk code stays
  dormant. `ENABLE_LEGACY_JWT` keeps the old token path reachable during a
  Clerk cutover.
