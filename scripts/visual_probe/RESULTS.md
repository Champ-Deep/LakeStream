# L1 results: 2026-10-19

Everything below ran live from a Windows 11 laptop (Python 3.12.10), direct
connection, no proxy. Raw records are in `data/visual_probe/<timestamp>/`
on that machine (gitignored). Keys were never written to any record.

## Compatibility matrix

| Component | What was tested | Result | Numbers |
|---|---|---|---|
| Browser: invisible_playwright 0.40.0 (invisible_core 50.48.0, patched Firefox 151) | headless launch, navigate, viewport + full-page PNG, HTML | works | launch 6–7.5 s, example.com load 1.4 s, first launch +52 MB GeoIP download |
| Browser: bot detection (bot.sannysoft.com, seed 42) | fingerprint scanner suite | all tests green; WebDriver "missing (passed)"; only the Chrome-object check is red, expected on any Firefox | status 200 |
| Vision: google/gemini-2.5-flash-lite via OpenRouter | screenshot → JSON observations | works | 5–9 s, 586–984 tokens, $0.0001–0.00025 per page |
| Vision: google/gemini-2.5-flash via OpenRouter | same | works, slightly richer topics | 7 s, 849 tokens, $0.0010 per page |
| Vision: deepseek/deepseek-chat via OpenRouter | image smoke test | **rejected**: HTTP 404 `No endpoints found that support image input` | – |
| Jev: api.typesafe.ai/v1/systemone (jev-1.13.0), text state | observations → one bounded action | works | 1.2 s, 963 in / 49 out tokens |
| Jev: same endpoint, screenshot inside state | image input | **rejected**: HTTP 400 `max_tokens_exceeded` (556 KB request) | – |
| Java controller | not run | the endpoint is plain HTTPS + JSON, so any Java HTTP client could call it; no JVM needed on the Python path | – |

## Page benchmark (logged out, direct IP)

| URL | HTTP | Outcome | Notes |
|---|---|---|---|
| example.com | 200 | ok | control |
| bot.sannysoft.com | 200 | ok | detection suite passed |
| linkedin.com/company/microsoft | 200 | ok | page fully rendered behind a sign-in pop-up; vision read name, industry, HQ, 29.3M followers, 10,001+ employees, specialties, affiliated pages |
| linkedin.com/in/satyanadella | 999 → authwall | login_wall | "Sign Up \| LinkedIn", stopped |
| linkedin.com/in/williamhgates | 999 → authwall | login_wall | stopped |
| github.com/Champ-Deep | 200 | ok | non-LinkedIn control |

Reached 4 / blocked 2 / failed 0. Company pages are reachable logged out;
individual profile pages are not from this IP without a session, and the
rules say stop there.

## Jev decision on the Microsoft page

Input: Gemini observations (company_page, overlay present, no posts visible).
Choice `CLICK_ACTIVITY` 0.36, `SCROLL` 0.35, `STOP` 0.29, `BACK` 0.00;
confidence 0.15. Low confidence is appropriate: the overlay hides part of
the page. The action set is bounded to open/click/scroll/back/stop.

## Fixes made during the run

- First vision attempt on the Microsoft page looped on the "About" text until
  the token cap and returned truncated JSON. Prompt now caps bio at 300
  chars, posts at 5, topics at 10, whole answer under 400 words.
- LinkedIn's sign-in form embeds a reCAPTCHA script on every public page,
  which LakeStream's `captcha_detector` matches. The probe now treats
  CAPTCHA markers as a block only alongside a challenge title, a block
  status or a tiny page.

## L2: screenshot-guided loop (same day, direct IP, logged out)

| URL | Outcome | Steps | Time | Model calls | Vision cost | Jev input tokens |
|---|---|---|---|---|---|---|
| linkedin.com/company/microsoft | reached posts | SCROLL, SCROLL, goal | 42 s | 5 | $0.0012 | 2.9k |
| linkedin.com/company/google | reached posts | SCROLL, SCROLL, goal | 50 s | 5 | $0.0006 | 2.9k |
| linkedin.com/company/amazon | reached posts | SCROLL (after re-ask), goal | 35 s | 4 | $0.0006 | 2.9k |
| linkedin.com/company/apple | end of page, no posts section on the public page | SCROLL ×4 | 46 s | 8 | $0.0007 | 5.7k |
| linkedin.com/in/satyanadella | stopped at landing: 999 → authwall | 0 | 13 s | 0 | $0 | 0 |

Journeys that reach posts cost about $0.001 in vision plus ~3k Jev input
tokens and take 35–50 s, most of it model latency (vision 4–15 s per step,
Jev 0.7–1.2 s). Evidence per step: before/after PNG, observations, Jev
probabilities. Example: Microsoft step 3 shows the "Updates" card with a
post from 3 days ago under the sign-in overlay.

Observed and fixed during L2:
- The vision model called LinkedIn's sign-in pop-up a "wall" even when the
  page behind it was readable, which pushed Jev to STOP on the first step
  (Amazon: STOP 0.76). The prompt now separates `login_or_access_wall`
  (content replaced) from `signin_popup_present` (overlay on content), Jev
  gets a facts header, and a STOP that contradicts the facts is re-asked once
  with the reason. Amazon then went STOP 0.65 → SCROLL 0.77 and found a post.
- Public company pages expose no Posts/Activity link logged out, so
  `CLICK_ACTIVITY` was never offered; posts are reached by scrolling to the
  "Updates" section. Apple's public page has no such section.
- Individual profile URLs never get past the landing check (authwall), so the
  loop makes no model calls there.

## L3: cited facts + outreach hooks, 6 public company pages

Run `l3_20261019_163018`, logged out, direct IP, sequential at one page per
~45 s. These are ad-hoc public pages; Deep's approved list is still pending.

| Page | Status | Name | Industry | Location | Bio | Posts | Topics | Other facts | Hooks | Time | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| microsoft | reached | Microsoft | missed | Redmond, Washington | no | 1 | 7 | 17 | 2 | 44 s | $0.0010 + 2.5k Jev |
| google | reached | Google | Software Development | Mountain View, CA | yes | 3 | 17 | 24 | 2 | 37 s | $0.0012 + 2.9k |
| amazon | reached | Amazon | Software Development | missed | no | 1 | 5 | 24 | 2 | 44 s | $0.0005 + 2.8k |
| meta | reached | Meta | Software Development | Menlo Park, CA | yes | 2 | 19 | 40 | 2 | 49 s | $0.0014 + 4.2k |
| netflix | reached | Netflix | Entertainment Providers | Los Gatos, CA | yes | 5 | 10 | 30 | 2 | 38 s | $0.0013 + 3.0k |
| nvidia | blocked | – | – | – | – | 0 | 0 | 0 | 0 | 9 s | $0 |

**Reached 5 / blocked 1 / failed 0.** NVIDIA returned HTTP 429 as the sixth
page in five minutes from one IP; probed once more four minutes later at
single-request pace it loaded (200). Recorded as blocked in the benchmark,
not re-run.

Every fact in the `.facts.json` files carries `source_url`, `captured_at`
and the screenshot file it was read from. All 10 hooks reference a valid
evidence id (`evidence_valid: true`) and are labelled "suggestion, not sent".

**Manual comparison, Microsoft** (`step_3.before.png` vs extraction):
post text ("This month, the Microsoft Atlanta team, in partnership with
Black in Technology, hosted the 404 Tech Summit…"), date label "3d", HQ
"1 Microsoft Way, Redmond, Washington 98052, US" and "10,001+ employees"
all match the screenshot exactly. Industry ("Software Development") was
visible on step 1 but not captured into `headline_or_role` on this page;
it was captured on the other four. Location for Amazon was missed the same
way. Field-level: 4 of 5 scalar fields correct where captured, 2 misses
across 5 pages, 0 wrong values.

**Hook quality:** post-based hooks are specific and faithful (Google's
Gemini 4 Argon post, Netflix's FIFA World Cup 2030 post). Fact-based hooks
fall back to generic lines ("with over 10,001 employees…") and two hooks
invent a seller value proposition ("our solutions can help…") that the
prompt did not supply. For the real run the hook prompt should be given the
sender's actual offer, or hooks restricted to posts only.

**Occlusion fix after this run:** the sign-in modal covered the centre of
every screenshot and some post excerpts were read garbled (Google:
"available glo precise long-horizon work"). The loop now closes the modal
before the first screenshot; the same Google post then reads "…made Gemini
4 Argon available globally. Designed for deep, precise long-horizon…".
The table above is from before that fix.

## L4: direct vs proxy

anyIP trial details have not arrived, so only the direct leg ran
(`l4_20261019_163548`, seed 42):

| URL | Direct outcome | Elapsed | Page load | HTML bytes | Proxy |
|---|---|---|---|---|---|
| example.com | ok, 200 | 12.5 s | 1.0 s | 2 KB | not_configured |
| linkedin.com/company/microsoft | ok, 200 | 19.3 s | 10.3 s | 525 KB | not_configured |

Exit-address check added 2026-10-20 (`l4_20261020_222220`): each leg first opens an IP-echo page in the
same kind of session, then looks the address up (ISP, ASN, hosting/proxy flags via ip-api.com, country
from the local GeoIP file as fallback). Direct leg from this laptop: **residential ISP (E Net
Entertainment, AS135188), Andhra Pradesh, India, hosting=false, proxy-flag=false**. That is what LinkedIn
sees today and why its public company pages render; a datacenter exit would carry `hosting=true` and is
the usual reason for challenges. The proxy leg will show the same fields for the anyIP session, so the
comparison is like-for-like: residential vs residential, or residential vs datacenter.

| Leg | Exit | Country / region | ISP / ASN | Hosting | example.com | linkedin.com/company/microsoft |
|---|---|---|---|---|---|---|
| direct | masked | India / Andhra Pradesh | E Net Entertainment / AS135188 | no | ok, 200, 19.3 s (load 1.1 s) | ok, 200, 19.2 s (load 3.1 s), 470 KB |
| proxy | not configured | – | – | – | – | – |

`proxy_compare.py` runs the proxy leg as soon as `VISUAL_PROBE_PROXY_*` is
set: one sticky session, same seed, same pace, cost floor from HTML bytes.
Cost per successful profile via proxy: **unknown** until a provider and
price are known. MiroFish (github.com/666ghj/MiroFish) was suggested as a
substitute; it is a multi-agent prediction/simulation engine, not a proxy
or IP service, so it does not apply to this task.

## L5: wired into LakeStream as an optional tier

Changes (all additive, default behaviour untouched):

| Where | What |
|---|---|
| `src/models/scraping.py` | `ScrapingTier.VISUAL`; optional `FetchResult.facts` |
| `src/scraping/fetcher/lake_visual_fetcher.py` | new fetcher wrapping the L2/L3 probe; returns a normal `FetchResult` plus `facts` with status `success` / `partial` / `blocked` / `failed` |
| `src/scraping/fetcher/factory.py` | registers the tier; never added to `escalation.py`, so only an explicit job `tier: "visual"` reaches it |
| `src/config/settings.py`, `.env.example` | `enable_visual_fetcher=false` (default), `typesafe_api_key`, model and budget knobs |
| `src/workers/content_worker.py` | facts stored as one `EXTRACTED` record next to the page record; a wall is stored as an explicit `blocked` record |
| `src/services/scraper.py`, `src/api/routes/scrape.py` | `POST /scrape/url` returns `facts` alongside markdown |
| `tests/unit/scraping/test_visual_fetcher.py`, `tests/unit/workers/test_content_worker_visual.py`, `tests/fixtures/visual/` | 14 tests, no browser or network: flag off, success with citations, wall → blocked, missing fields null, loop error → failed, factory mapping, fixture classification, worker record handling, other tiers unchanged |

`tests/unit/scraping` + `tests/unit/workers`: HEAD 134 passed → branch 148 passed.

Live, 2026-10-19 evening, API + arq worker on the laptop against Dockerised Postgres/Redis, `ENABLE_VISUAL_FETCHER=true`:

- `POST /api/scrape/url {"url": ".../company/microsoft/", "tier": "visual"}` → HTTP 200 in 49 s: `tier_used: visual`, markdown of the page, `facts.status: success`, 3 steps, 5 model calls, $0.0011 vision, name "Microsoft", location "Redmond, Washington", one cited post (date "4d", source URL, `step_3.before.png`); evidence under `data/screenshots/visual/<url-hash>/<ts>/` with `trace.json`.
- `POST /api/scrape/execute {"domain": "www.linkedin.com/company/microsoft", "tier": "visual", "max_pages": 1}` → job `completed` in 132 s; `GET /api/export/json/{job_id}` returns 4 records: `page` + `extracted` (status success, 2 posts, location) for the company URL, and `page` + `extracted` (status partial, "max steps") for a second URL the domain mapper discovered (LinkedIn's user-agreement page).

Caveats found while wiring it in:
- A job runs every discovered URL through the override tier; the mapper adds a homepage fallback even at `max_pages: 1`, so the visual tier spent 8 steps on a legal page. For now use the tier on explicit single URLs (`/scrape/url`) or seed lists where every URL is a target; a per-URL allow-list is the next step.
- The probe modules still live in `scripts/visual_probe` and are imported from there by the fetcher; promoting them to `src/scraping/visual/` is a follow-up once the approach is accepted.
- Environment: `uv pip install -e .[dev]` resolves selectolax 1.x, whose `selectolax.parser` import now raises; the repo pins only `>=0.3.0`. Pinned `<1.0` locally; the pin should move into `pyproject.toml`/`requirements.txt`.
- `Settings` forbids unknown env keys: any stray key in `.env` makes every `get_settings()` call fail (and the app with it). Keep `.env` to known keys.

## Blockers / needs

- Deep's approved URL list (the pages above were ad-hoc tests, not client targets).
- anyIP trial details for the L4 direct-vs-proxy comparison.
- Decision: keep invisible_playwright or switch to Camoufox (two-line change either way).
- Both API keys were shared over chat; rotate them after the assignment.
