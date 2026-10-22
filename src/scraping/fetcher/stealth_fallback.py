"""Second chance for pages that refuse the normal Playwright browser.

When a Playwright fetch comes back blocked (403 / 429 / 503, an "Access Denied"
or "Just a moment..." page, a captcha), the fetcher calls :func:`rescue`. It
tries stronger browsers in order and returns the first page that is not a
refusal:

1. ``invisible_firefox`` - invisible_playwright's patched Firefox with a
   coherent human fingerprint and human-like input, plus a wait for bot-check
   pages (Cloudflare, PerimeterX, Akamai, DataDome) to clear on their own.
2. ``scrapling`` - Scrapling's StealthyFetcher (patchright Chromium) with its
   Cloudflare Turnstile / interstitial solver. Scrapling is already a project
   dependency and ``scrapling install`` already runs in the Docker images.

Measured on 22 Oct 2026 from a home connection: the normal browser loaded 5 of
15 protected B2B pages; engine 1 loaded all 8 of the hardest of those
(Salesforce, G2, Crunchbase, Glassdoor, Trustpilot, Indeed, Capterra, Zillow)
in 4-23 s each. Engine 2 also passed Crunchbase and Glassdoor's Cloudflare
checks when everything else had failed.

An engine that is not installed is skipped with a log line, never an error.
The org's proxy, when one is configured, is passed through to every engine.
Behind ``playwright_stealth_fallback`` (on by default); the engine order is
``playwright_stealth_engines``.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from src.scraping.fetcher.captcha_detector import detect_captcha

log = structlog.get_logger()

# Pages that are a refusal even though the HTML is not tiny.
_BLOCK_PAGE = re.compile(
    r"<title[^>]*>\s*(?:Access Denied|Access to this page has been denied|Attention Required!"
    r"|Just a moment\.\.\.|Pardon Our Interruption|Request unsuccessful|Blocked\b|Robot or human)",
    re.I,
)
# Bot-check interstitials that usually clear by themselves after a few seconds.
# Deliberately NOT "challenge-platform": Cloudflare injects
# /cdn-cgi/challenge-platform/scripts/jsd/... into ordinary pages too.
_CHALLENGE = re.compile(
    r"Just a moment|Attention Required|Checking your browser|cf-challenge|challenge-running"
    r"|challenge-form|cf_chl_|captcha-delivery|px-captcha|Press &amp; Hold"
    r"|Pardon Our Interruption",
    re.I,
)
REFUSED_STATUS = frozenset({401, 403, 405, 429, 503})
MIN_REAL_PAGE_BYTES = 1500
# A page this big, with a normal title and no interstitial marker, is the
# content, whatever the status says and even if it embeds a captcha widget in a
# form. Trustpilot answers 403 with its full 1 MB reviews page, which also holds
# a reCAPTCHA box. Real block pages are small (Akamai 0.3 KB, PerimeterX 10 KB,
# Cloudflare 10-40 KB) or carry a block title (Glassdoor's 116 KB challenge is
# titled "Just a moment...").
REAL_PAGE_BYTES = 100_000
NETWORKIDLE_MS = 10_000


def _block_page_or_interstitial(html: str) -> bool:
    head = html[:30_000]
    return bool(_BLOCK_PAGE.search(head) or _CHALLENGE.search(head))


def looks_refused(status: int, html: str) -> bool:
    """True when (status, html) is a refusal rather than the real page."""
    html = html or ""
    if status == 0 or len(html) < MIN_REAL_PAGE_BYTES:
        return True
    if _block_page_or_interstitial(html):
        return True
    if len(html) >= REAL_PAGE_BYTES:
        return False
    return status in REFUSED_STATUS or detect_captcha(html)


def should_rescue(status: int, html: str, blocked: bool, captcha: bool) -> bool:
    """Whether a normal fetch result is a refusal worth a stealth retry.

    ``blocked`` / ``captcha`` come from the fetch layer's own detector
    (BaseFetcher.is_blocked). A small page that loaded fine is not a refusal;
    a big real page served with an error status or a captcha widget in a form
    is not either.
    """
    if _BLOCK_PAGE.search((html or "")[:30_000]):
        return True
    if blocked or captcha:
        return looks_refused(status, html)
    return False


@dataclass
class Rescue:
    """A page fetched by a stealth engine."""

    engine: str
    status_code: int
    html: str
    headers: dict[str, str] = field(default_factory=dict)
    screenshot_bytes: bytes | None = None
    seconds: float = 0.0

    @property
    def refused(self) -> bool:
        return looks_refused(self.status_code, self.html)


# --------------------------------------------------------------------- engines


async def _wait_for_challenge(page, status_box: dict, max_wait_s: int) -> str:
    """Re-read the page until a bot check clears, or give up after max_wait_s."""
    html = await page.content()
    waited = 0.0
    while waited < max_wait_s and (
        _CHALLENGE.search(html[:30_000]) or status_box["status"] in REFUSED_STATUS
    ):
        await page.wait_for_timeout(2000)
        waited += 2
        try:
            html = await page.content()
        except Exception:  # page navigating away from the challenge
            continue
    return html


async def _invisible_firefox(
    url: str,
    *,
    timeout_ms: int,
    headless: bool,
    proxy: dict | None,
    screenshot: bool,
    max_wait_s: int,
) -> Rescue:
    from invisible_playwright.async_api import InvisiblePlaywright  # optional dependency

    status_box: dict[str, Any] = {"status": 0, "headers": {}}
    async with InvisiblePlaywright(headless=headless, humanize=True, proxy=proxy) as browser:
        page = await browser.new_page()

        def _on_response(resp) -> None:
            try:
                if resp.request.is_navigation_request() and resp.frame == page.main_frame:
                    status_box["status"] = resp.status
                    status_box["headers"] = dict(resp.headers)
            except Exception:
                pass

        page.on("response", _on_response)
        await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=NETWORKIDLE_MS)
        except Exception:
            pass
        html = await _wait_for_challenge(page, status_box, max_wait_s)
        shot = await page.screenshot(full_page=True) if screenshot else None
        return Rescue("invisible_firefox", status_box["status"], html, status_box["headers"], shot)


async def _scrapling(
    url: str,
    *,
    timeout_ms: int,
    headless: bool,
    proxy: dict | None,
    screenshot: bool,
    max_wait_s: int,
) -> Rescue:
    from scrapling.fetchers import StealthyFetcher  # project dependency

    kwargs: dict[str, Any] = {
        "headless": headless,
        "solve_cloudflare": True,
        "network_idle": False,
        "timeout": timeout_ms,
        "wait": 3000,
    }
    if proxy:
        kwargs["proxy"] = {
            k: v for k, v in proxy.items() if k in ("server", "username", "password")
        }
    resp = await StealthyFetcher.async_fetch(url, **kwargs)
    body = getattr(resp, "html_content", None)
    if body is None:
        body = getattr(resp, "body", b"")
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    headers = {str(k): str(v) for k, v in dict(getattr(resp, "headers", {}) or {}).items()}
    return Rescue("scrapling", int(getattr(resp, "status", 0) or 0), body, headers, None)


ENGINES = {
    "invisible_firefox": _invisible_firefox,
    "scrapling": _scrapling,
}


# ------------------------------------------------------------------- entry point


def engine_order(settings) -> list[str]:
    raw = getattr(settings, "playwright_stealth_engines", "invisible_firefox,scrapling") or ""
    return [e.strip() for e in raw.split(",") if e.strip() in ENGINES]


async def rescue(
    url: str, settings, *, proxy: dict | None = None, screenshot: bool = False
) -> Rescue | None:
    """Try each stealth engine; return the first page that is not a refusal, else None."""
    if not getattr(settings, "playwright_stealth_fallback", True):
        return None
    timeout_ms = max(int(getattr(settings, "playwright_timeout_ms", 30_000)), 60_000)
    max_wait_s = int(getattr(settings, "playwright_challenge_wait_s", 30))
    headless = bool(getattr(settings, "playwright_headless", True))
    budget_s = (timeout_ms / 1000) + max_wait_s + NETWORKIDLE_MS / 1000 + 30

    for name in engine_order(settings):
        start = time.time()
        try:
            result = await asyncio.wait_for(
                ENGINES[name](
                    url,
                    timeout_ms=timeout_ms,
                    headless=headless,
                    proxy=proxy,
                    screenshot=screenshot,
                    max_wait_s=max_wait_s,
                ),
                timeout=budget_s,
            )
        except ImportError as exc:
            log.info("stealth_engine_unavailable", engine=name, url=url, error=str(exc))
            continue
        except Exception as exc:  # launch failure, timeout, network
            log.warning(
                "stealth_engine_error",
                engine=name,
                url=url,
                error=str(exc)[:200],
                error_type=type(exc).__name__,
            )
            continue
        result.seconds = round(time.time() - start, 1)
        if not result.refused:
            log.info(
                "stealth_rescue_ok",
                engine=name,
                url=url,
                status=result.status_code,
                seconds=result.seconds,
            )
            return result
        log.info(
            "stealth_engine_refused",
            engine=name,
            url=url,
            status=result.status_code,
            seconds=result.seconds,
        )
    return None
