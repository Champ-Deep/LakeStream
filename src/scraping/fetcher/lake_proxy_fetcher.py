import asyncio
import time

from scrapling.fetchers import StealthyFetcher

from src.config.constants import TIER_COSTS
from src.config.settings import get_settings
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier


class LakeProxyFetcher:
    """Tier 3: Stealth browser + residential proxy."""

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        options = options or FetchOptions()
        settings = get_settings()
        start = time.time()

        proxy_url = settings.brightdata_proxy_url or settings.smartproxy_url

        try:
            fetcher = StealthyFetcher(auto_match=False)
            proxy_config = {"server": proxy_url} if proxy_url else None

            response = await asyncio.to_thread(
                fetcher.fetch,
                url,
                headless=True,
                network_idle=True,
                timeout=options.timeout,
                proxy=proxy_config,
            )
            html = response.html_content
            status_code = response.status
            blocked = status_code in (403, 429, 503) or len(html) < 200
            captcha = self._detect_captcha(html)
        except Exception:
            html = ""
            status_code = 0
            blocked = True
            captcha = False

        duration_ms = int((time.time() - start) * 1000)

        return FetchResult(
            url=url,
            status_code=status_code,
            html=html,
            headers={},
            tier_used=ScrapingTier.HEADLESS_PROXY,
            cost_usd=TIER_COSTS["headless_proxy"],
            duration_ms=duration_ms,
            blocked=blocked,
            captcha_detected=captcha,
        )

    def _detect_captcha(self, html: str) -> bool:
        captcha_signals = [
            "captcha",
            "challenge-form",
            "cf-browser-verification",
            "recaptcha",
            "hcaptcha",
            "turnstile",
        ]
        html_lower = html.lower()
        return any(signal in html_lower for signal in captcha_signals)
