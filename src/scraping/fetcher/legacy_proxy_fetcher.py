import time

from src.config.constants import TIER_COSTS
from src.config.settings import get_settings
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier


class ProxyFetcher:
    """Tier 3: Headless browser + residential proxy fetcher."""

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        options = options or FetchOptions()
        settings = get_settings()
        start = time.time()

        proxy_url = settings.brightdata_proxy_url or settings.smartproxy_url
        if not proxy_url:
            # Fall back to browser fetcher without proxy
            from src.scraping.fetcher.browser_fetcher import BrowserFetcher

            fetcher = BrowserFetcher()
            result = await fetcher.fetch(url, options)
            # Override tier and cost
            result.tier_used = ScrapingTier.HEADLESS_PROXY
            result.cost_usd = TIER_COSTS["headless_proxy"]
            return result

        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    proxy={"server": proxy_url},
                )
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1920, "height": 1080},
                )
                page = await context.new_page()

                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=options.timeout,
                )

                if options.wait_for_selector:
                    await page.wait_for_selector(options.wait_for_selector, timeout=10000)

                html = await page.content()
                status_code = response.status if response else 0
                headers = dict(response.headers) if response else {}

                await browser.close()

            blocked = status_code in (403, 429, 503) or len(html) < 200
            captcha = any(sig in html.lower() for sig in ["captcha", "challenge-form", "recaptcha"])

        except Exception:
            html = ""
            status_code = 0
            headers = {}
            blocked = True
            captcha = False

        duration_ms = int((time.time() - start) * 1000)

        return FetchResult(
            url=url,
            status_code=status_code,
            html=html,
            headers=headers,
            tier_used=ScrapingTier.HEADLESS_PROXY,
            cost_usd=TIER_COSTS["headless_proxy"],
            duration_ms=duration_ms,
            blocked=blocked,
            captcha_detected=captcha,
        )
