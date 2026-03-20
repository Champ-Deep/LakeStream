import asyncio
import time

import structlog
from scrapling.fetchers import Fetcher

from src.config.constants import TIER_COSTS
from src.config.settings import get_settings
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier
from src.scraping.fetcher.captcha_detector import detect_captcha

log = structlog.get_logger()


class LakeLightPandaFetcher:
    """Tier 0: Cheapest/fastest tier.

    When LIGHTPANDA_WS_URL is configured: connects to a real LightPanda
    browser via CDP WebSocket (Zig-based, 11x faster than Chrome).

    When not configured: falls back to scrapling's basic HTTP Fetcher
    (still cheap, no browser overhead).

    Cost: $0.001 per request
    Captcha detection: enabled
    """

    def __init__(self):
        self._http_fetcher = Fetcher()

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        settings = get_settings()
        if settings.lightpanda_ws_url:
            return await self._fetch_cdp(url, options, settings)
        return await self._fetch_http(url, options, settings)

    async def _fetch_cdp(
        self, url: str, options: FetchOptions | None, settings: object,
    ) -> FetchResult:
        """Fetch via LightPanda CDP WebSocket (real headless browser)."""
        from playwright.async_api import async_playwright

        options = options or FetchOptions()
        start = time.time()

        try:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(settings.lightpanda_ws_url)
                context = await browser.new_context()
                page = await context.new_page()

                timeout = options.timeout or settings.playwright_timeout_ms
                response = await page.goto(url, timeout=timeout)

                # Shorter networkidle wait — LightPanda is fast
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass  # Non-fatal

                html = await page.content()
                status_code = response.status if response else 0

                await browser.close()

            http_error = status_code in (403, 429, 503)
            tiny_html = len(html) < settings.min_html_bytes
            captcha = detect_captcha(html) if html else False
            blocked = http_error or tiny_html

        except Exception as exc:
            log.warning(
                "lightpanda_cdp_error",
                url=url,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            html = ""
            status_code = 0
            blocked = True
            captcha = False

        duration_ms = int((time.time() - start) * 1000)

        log.debug(
            "lightpanda_cdp_fetch",
            url=url,
            status=status_code,
            blocked=blocked,
            captcha=captcha,
            duration_ms=duration_ms,
        )

        return FetchResult(
            url=url,
            status_code=status_code,
            html=html,
            headers={},
            tier_used=ScrapingTier.LIGHTPANDA,
            cost_usd=TIER_COSTS["lightpanda"],
            duration_ms=duration_ms,
            blocked=blocked,
            captcha_detected=captcha,
        )

    async def _fetch_http(
        self, url: str, options: FetchOptions | None, settings: object,
    ) -> FetchResult:
        """Fallback: basic HTTP fetch via scrapling when LightPanda not available."""
        options = options or FetchOptions()
        start = time.time()

        try:
            response = await asyncio.to_thread(
                self._http_fetcher.get,
                url,
                timeout=options.timeout / 1000,
            )
            html = response.html_content
            status_code = response.status

            http_error = status_code in (403, 429, 503)
            tiny_html = len(html) < settings.min_html_bytes
            captcha = detect_captcha(html) if html else False
            blocked = http_error or tiny_html

        except Exception as exc:
            log.warning(
                "lightpanda_http_fallback_error",
                url=url,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            html = ""
            status_code = 0
            blocked = True
            captcha = False

        duration_ms = int((time.time() - start) * 1000)

        log.debug(
            "lightpanda_http_fetch",
            url=url,
            status=status_code,
            blocked=blocked,
            captcha=captcha,
            duration_ms=duration_ms,
        )

        return FetchResult(
            url=url,
            status_code=status_code,
            html=html,
            headers={},
            tier_used=ScrapingTier.LIGHTPANDA,
            cost_usd=TIER_COSTS["lightpanda"],
            duration_ms=duration_ms,
            blocked=blocked,
            captcha_detected=captcha,
        )
