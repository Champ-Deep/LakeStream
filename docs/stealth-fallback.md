# Stealth retry for refused pages

Branch `feature/sprint-track-b`, 22 Oct 2026. Code: `src/scraping/fetcher/stealth_fallback.py`.

## Problem

Many B2B research sources sit behind bot protection (Akamai, Cloudflare,
PerimeterX, DataDome). The Playwright fetcher's headless Chromium was refused
on 10 of 15 such pages. The existing "stronger" tier, `playwright_proxy`, only
helps when a proxy is configured; without one it opens the same browser the same
way and is refused again (and `_build_tier_order` skips it entirely when no
proxy is available).

## What changed

```text
LakePlaywrightFetcher.fetch / LakePlaywrightProxyFetcher.fetch
  normal headless Chromium (fast, cheap)        # unchanged
  if refused (blocked / captcha / block page):
    stealth_fallback.rescue(url)                # new
      1. invisible_firefox: patched Firefox, human fingerprint + input,
         waits up to 30 s for bot-check interstitials to clear
      2. scrapling: StealthyFetcher (patchright Chromium) with Cloudflare solver
      first result that is not a refusal wins -> blocked=False
  elif error status / captcha widget but the body is plainly the real page:
    keep it (blocked=False)                     # new
```

- The org proxy, when configured, is passed to both engines.
- A missing engine is skipped with a log line, never an error.
- Settings: `playwright_stealth_fallback=true`, `playwright_stealth_engines=invisible_firefox,scrapling`,
  `playwright_challenge_wait_s=30`.
- Dependencies: `invisible-playwright>=0.40.0` added (Scrapling and patchright were
  already project dependencies). Both Dockerfiles pre-download the patched Firefox
  and its GeoIP file at build time; the API image also installs Firefox system libraries.

**"Is it really refused?"** A page counts as refused when it has a block title
("Access Denied", "Just a moment...", "Access to this page has been denied" ...), an
interstitial marker, or is small and has an error status or captcha. A page of
100 KB or more with a normal title and no interstitial marker is the real page,
even with a 403 status or a captcha box inside a form: Trustpilot answers 403 with
its full 1 MB reviews page, which also contains a reCAPTCHA widget.

## Evidence

Which browser setup gets through (same 15 protected pages, 22 Oct, home connection, no proxy):

| Setup | Pages fetched |
|---|---:|
| Today's headless Chromium | 5 / 15 |
| Same, disguised (real UA, locale, `webdriver` hidden) | 6 / 15 |
| Installed Google Chrome, headless, disguised | 8 / 15 |
| invisible_firefox (no wait) | 10 / 15 |
| invisible_firefox with challenge wait, on the 8 hardest | 8 / 8 |
| Scrapling StealthyFetcher with Cloudflare solver | 13 / 15 |

The real fetcher with the retry wired in (`python benchmarks/refusal_bench.py`):

| Run | Pages fetched | Notes |
|---|---:|---|
| Normal browser only (before) | 5 / 15 | |
| With retry, run 1 | 14 / 15 | Trustpilot's real 403 page was still being thrown away |
| With retry, run 2 | 13 / 15 | same Trustpilot issue; G2 refused both engines this time |
| With retry, final code (403 / captcha-widget fix) | 10 / 10 | stopped after 10 sites when the laptop ran low on memory; G2 and Trustpilot both fetched; the 5 sites not reached (Adobe, Zillow, Walmart, Nike, Home Depot) were fetched in both earlier runs |

How each page was fetched in the final run: normal browser for Oracle, IBM and
Trustpilot (kept despite its 403); invisible_firefox for Salesforce, G2,
Crunchbase, Glassdoor, Indeed and Capterra (20-32 s each); Scrapling for
Similarweb (72 s).

Rescued pages take 15-75 s instead of 5-15 s; pages the normal browser gets are
unaffected (the retry only runs on refusals).

## Limits

No tool gets every page every time. Protection vendors score the IP address as
well as the browser, so the same site can pass on one run and refuse on the next
(G2, which uses DataDome, passed in one run today and refused in the next after
many visits from the same home IP). For those, the next step is a residential
proxy, which the retry already uses when one is configured.

## Tests

`tests/unit/scraping/test_stealth_fallback.py`: refusal detection on Cloudflare,
PerimeterX and Akamai pages; big real pages with a 403 or a captcha widget are
kept; small pages that loaded fine are not retried; engine order, skipping missing
or crashing engines, the setting; and the real fetcher (Chromium, local server)
rescuing a 403 "Access Denied" page, staying blocked when every engine fails, and
never retrying a normal page.
