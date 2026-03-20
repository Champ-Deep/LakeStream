import asyncio
import time

import structlog
from scrapling.fetchers import Fetcher

from src.config.constants import TIER_COSTS
from src.config.settings import get_settings
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier

log = structlog.get_logger()

# CAPTCHA indicator patterns (checked in lowercased HTML)
_CAPTCHA_PATTERNS = [
    "recaptcha",
    "g-recaptcha",
    "hcaptcha",
    "cf-turnstile",
    "captcha",
    "are you a robot",
    "verify you are human",
    "please verify you",
    "bot detection",
    "cloudflare ray id",
    "ddos-guard",
    "i am not a robot",
]


def _detect_captcha(html: str) -> bool:
    """Detect CAPTCHA/bot-check pages by scanning for known patterns."""
    lower = html.lower()
    return any(pattern in lower for pattern in _CAPTCHA_PATTERNS)


class LakeLightPandaFetcher:
    """Tier 1: Ultra-fast HTTP fetcher using LightPanda engine via scrapling.

    LightPanda is a lightweight headless browser written in Zig — significantly
    faster and cheaper than Playwright for sites that don't require heavy
    anti-bot evasion. Used as the first-pass tier before escalating.

    Cost: $0.00005 per request (cheapest tier)
    Captcha detection: enabled
    """

    def __init__(self):
        self.fetcher = Fetcher()

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        options = options or FetchOptions()
        settings = get_settings()
        start = time.time()

        try:
            response = await asyncio.to_thread(
                self.fetcher.get,
                url,
                timeout=options.timeout / 1000,
            )
            html = response.html_content
            status_code = response.status

            http_error = status_code in (403, 429, 503)
            tiny_html = len(html) < settings.min_html_bytes
            captcha = _detect_captcha(html) if html else False
            # blocked = hard failure (bad status or empty). Captcha is a soft
            # signal — triggers escalation but does not discard the HTML.
            blocked = http_error or tiny_html

        except Exception as exc:
            log.warning(
                "lake_lightpanda_fetcher_error",
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
            "lake_lightpanda_fetch",
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
