import asyncio
import time

import structlog
from scrapling.fetchers import StealthyFetcher

from src.config.constants import TIER_COSTS
from src.config.settings import get_settings
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier

log = structlog.get_logger()


class LakeProxyFetcher:
    """Tier 3: Stealth browser + residential proxy."""

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        options = options or FetchOptions()
        settings = get_settings()
        start = time.time()

        proxy_url = settings.brightdata_proxy_url or settings.smartproxy_url

        try:
            fetcher = StealthyFetcher()
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
            http_error = status_code in (403, 429, 503)
            tiny_html = len(html) < settings.min_html_bytes
            blocked = http_error or tiny_html
            captcha = False  # Disabled: pattern detection caused false positives
        except Exception as exc:
            log.warning(
                "lake_proxy_fetcher_error", url=url, error=str(exc), error_type=type(exc).__name__
            )
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

