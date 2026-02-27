import asyncio
import time

from scrapling.fetchers import Fetcher

from src.config.constants import TIER_COSTS
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier


class LakeFetcher:
    """Tier 1: Fast HTTP fetcher using Lake's fetching engine."""

    def __init__(self):
        self.fetcher = Fetcher(auto_match=False)

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        options = options or FetchOptions()
        start = time.time()

        try:
            response = await asyncio.to_thread(
                self.fetcher.get,
                url,
                timeout=options.timeout / 1000,
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
            tier_used=ScrapingTier.BASIC_HTTP,
            cost_usd=TIER_COSTS["basic_http"],
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
