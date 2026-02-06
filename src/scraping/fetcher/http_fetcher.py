import time

import httpx

from src.config.constants import TIER_COSTS
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier


class HttpFetcher:
    """Tier 1: Basic HTTP fetcher using httpx."""

    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    }

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        options = options or FetchOptions()
        timeout = options.timeout / 1000  # ms to seconds
        headers = {**self.DEFAULT_HEADERS, **options.headers}

        start = time.time()
        blocked = False
        captcha = False

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=timeout,
                headers=headers,
            ) as client:
                response = await client.get(url)
                html = response.text
                status_code = response.status_code

                # Detect blocks
                blocked = status_code in (403, 429, 503)
                captcha = self._detect_captcha(html)
                if not blocked and len(html) < 200:
                    blocked = True

        except httpx.TimeoutException:
            html = ""
            status_code = 408
            blocked = True
        except httpx.HTTPError:
            html = ""
            status_code = 0
            blocked = True

        duration_ms = int((time.time() - start) * 1000)

        return FetchResult(
            url=url,
            status_code=status_code,
            html=html,
            headers=dict(response.headers) if "response" in dir() else {},
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
