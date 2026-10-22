# Resource blocking and regression harness

Sprint 21-22 Oct 2026, Track B. Branch `feature/sprint-track-b`, based on
`v2-consolidation` (upstream `main` is six months behind and lacks the v2
content-hash pipeline the harness covers).

## Day 1: resource blocking

**What changed**

| File | Change |
|------|--------|
| `src/scraping/fetcher/resource_blocking.py` | New. Decides per request what to drop and installs a `page.route("**/*")` handler. |
| `src/scraping/fetcher/lake_playwright_fetcher.py` | Installs blocking on every page (tier 2.5). |
| `src/scraping/fetcher/lake_playwright_proxy_fetcher.py` | Same for tier 3, where it matters most: residential proxy traffic is billed per GB. |
| `src/config/settings.py` | `playwright_block_resources: bool = True` (env `PLAYWRIGHT_BLOCK_RESOURCES=false` turns it off). |

**Rules**

- Dropped: `image`, `media`, `font`, `ping` requests, and any request to a listed
  ad / analytics / tracking host or its subdomains (Google Analytics / Tag Manager /
  DoubleClick, Meta and LinkedIn pixels, Hotjar, Segment, Mixpanel, Amplitude,
  HubSpot analytics, 6sense, Demandbase, ZoomInfo WebSights, ad exchanges, and so on).
- Never dropped: the page's own navigation; scripts, XHR / fetch and stylesheets
  (they build the DOM the parsers read); chat widgets, consent managers, form
  providers and CDNs (they can carry content).
- A tracker vendor's own site is first-party: scraping `amplitude.com` loads
  `cdn.amplitude.com` normally; the same host on someone else's page is blocked.
- When a screenshot is requested (visual tier), images and fonts load; trackers
  are still dropped.
- Allowed requests use `route.fallback()`, so other route handlers still run.

**Proof that the structured extractors keep the full DOM**

`tests/unit/scraping/test_resource_blocking.py::test_structured_extractors_get_the_full_dom_with_blocking_on`
runs the real `LakePlaywrightFetcher` (real Chromium, Redis stubbed) against two
local servers: a site whose pricing cards are rendered by first-party JS from a
`fetch('/plans.json')`, plus team cards and JSON-LD, and a second host acting as a
tracker. With blocking off and on:

- `PricingParser` returns the same three plans and `ContactParser` the same people;
- `/app.js`, `/plans.json`, `/style.css` are still requested;
- the image, font, video and tracker are requested only with blocking off.

38 more tests cover the decision rules (lookalike domains, first-party vendors and
their subdomains, country second-level domains such as `.co.uk`, screenshot mode,
the setting).

**Bytes per page, same 20 URLs** (`benchmarks/resource_blocking_bench.py`)

Run on 22 Oct 2026 from a home connection, headless Chromium, fresh context per
load, off and on alternated per URL. Bytes are Chrome DevTools
`encodedDataLength` (headers + body over the wire).

| | blocking off | blocking on |
|---|---:|---:|
| total, 19 pages | 92.5 MB | 64.0 MB |
| mean per page | 4.87 MB | 3.37 MB |
| median load time | 11.2 s | 6.4 s |

**30.8% fewer bytes overall, median 35.5% per page (range 5% to 88%).** Salesforce
returned 403 to the headless browser both times and is excluded (an escalation case,
not a blocking one).

| # | URL | off MB | on MB | saved |
|--:|-----|------:|------:|------:|
| 1 | hubspot.com/pricing/marketing | 4.69 | 2.52 | 46.2% |
| 2 | atlassian.com/software/jira/pricing | 4.49 | 2.90 | 35.5% |
| 3 | slack.com/pricing | 3.20 | 1.80 | 43.7% |
| 4 | notion.com/pricing | 3.27 | 1.58 | 51.7% |
| 5 | zendesk.com/pricing | 3.72 | 2.16 | 42.0% |
| 6 | mailchimp.com/pricing/marketing | 4.39 | 3.51 | 19.9% |
| 7 | dropbox.com/plans | 2.86 | 2.46 | 14.1% |
| 8 | zoom.us/pricing | 9.20 | 7.64 | 16.9% |
| 9 | asana.com/pricing | 3.79 | 2.50 | 34.2% |
| 10 | monday.com/pricing | 7.58 | 6.51 | 14.1% |
| 11 | intercom.com/pricing | 2.14 | 1.57 | 26.8% |
| 12 | shopify.com/pricing | 1.80 | 0.88 | 51.0% |
| 13 | about.gitlab.com/pricing | 3.35 | 1.04 | 69.1% |
| 14 | cloudflare.com/plans | 2.50 | 2.14 | 14.5% |
| 15 | freshworks.com/crm/pricing | 18.06 | 17.09 | 5.4% |
| 16 | semrush.com/prices | 3.64 | 0.43 | 88.1% |
| 17 | hubspot.com/company/management | 3.62 | 1.46 | 59.7% |
| 18 | salesforce.com/company/leadership | 403 | 403 | excluded |
| 19 | lakeb2b.com | 2.83 | 0.82 | 70.9% |
| 20 | zoominfo.com/about | 7.41 | 4.99 | 32.6% |

Pages that save least (Freshworks, monday, Zoom) are mostly first-party JavaScript,
which is deliberately never blocked.

**Extraction parity on the live pages:** pricing_parser and contact_parser returned
identical output on 17 of 19 pages. The 2 differences are both false positives that
blocking removes, not lost content:

- semrush.com: the "person" is a Sentry DSN key (`<hex>@www.semrush.com`) in an
  inline script loaded through Google Tag Manager.
- zoominfo.com: an email inside a Snap pixel script that Tag Manager injects.

**Fixed (22 Oct, Ajay's request): contact parser reads only real contact data.**
`ContactParser` used to scan text inside `<script>` tags, which is how it picked
those up. It now scans only text a visitor can see (`script`, `style`, `noscript`,
`template`, `svg` stripped) plus the targets of visible `mailto:` and
`linkedin.com/in/` links. JSON-LD `Person` data is still read from its script tag.
Words from separate tags are no longer glued together. After the fix, zoominfo.com
returns only the real CEO LinkedIn profile, with blocking on and off; the fake
email is gone. 5 tests in `tests/unit/scraping/test_resource_blocking.py`.

Reproduce: `python benchmarks/resource_blocking_bench.py`. Results are written to
`data/bench/` (git-ignored; only numbers, no page content).

## Day 2: regression harness

`tests/unit/test_regression_harness.py`, 17 tests, no DB / Redis / network.
Fetchers and the `page_content` table are in-memory fakes; everything else is
production code.

| Area | What is pinned |
|------|----------------|
| `should_escalate`, captcha path | Real Cloudflare "Just a moment" and reCAPTCHA pages, run through `BaseFetcher.is_blocked`, escalate PLAYWRIGHT to PLAYWRIGHT_PROXY with no wait. |
| `should_escalate`, blocked path | 403 escalates with no wait; 429 / 503 wait first; a tiny 200 is a soft block; blocked at the last tier stops and records a failure; a clean page never escalates. |
| Cache hit skips extraction | Unchanged `content_hash`: the page is fetched, the page record saved, specialized extraction skipped, no change event. Whitespace-only reflow is still a hit. |
| Changed content | A changed price is re-extracted and a change is recorded with old and new hash. `force_refresh` and `enable_scrape_cache=false` always re-extract. A blocked fetch touches neither extraction nor the cache. |

Mutation check: making `should_escalate` ignore captchas fails 3 tests; making
the cache never hit fails 2. Both files were restored afterwards.

**Changed (22 Oct, Ajay's request): unchanged pages are extracted every time.**
`enable_scrape_cache` now defaults to `false`, so every fetch re-runs extraction,
even when `content_hash` has not changed. The hash is still stored and real changes
are still recorded. The skip-on-unchanged shortcut is kept behind the flag
(`ENABLE_SCRAPE_CACHE=true`); the cache tests above force it on and still pass,
and `test_default_settings_extract_unchanged_pages_every_time` pins the new default.
Trade-off: repeated crawls of unchanged pages now pay for extraction again
(including LLM extraction when a job uses it).

Note: the fetch itself always happened, cache or not, because the hash is computed
from the downloaded page.

**Bake-off.** `benchmarks/bakeoff/` does not exist on any branch of
`Champ-Deep/LakeStream` or the fork. The only benchmarks are `benchmarks/lake_benchmark.py`
and `tech_engine_benchmark.py`, which hit live domains, so there is no offline
bake-off to run yet.

## Suite

| | before | after |
|---|---:|---:|
| unit tests passed | 560 | 621 |
| failed (pre-existing: `Wappalyzer` package missing in the local venv; CI installs it) | 3 | 3 |

Run from a folder without a `.env`, as CI does; a local `.env` with keys this
branch's `Settings` does not declare (for example `TYPESAFE_API_KEY` from the
visual-probe branch) fails `Settings` validation at import.
