"""Go HTTP fetcher tier — delegates to the fast go-http-fetcher sidecar.

Experimental (flag: enable_go_fetchers). Maps the sidecar's JSON response to
FetchResult exactly like the in-process fetchers.
"""

import base64
import time

import httpx
import structlog

from src.config.constants import TIER_COSTS
from src.config.settings import get_settings
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier

log = structlog.get_logger()


class LakeGoHttpFetcher:
    """Tier: fast HTTP fetch via the Go sidecar (no JS rendering)."""

    tier = ScrapingTier.GO_HTTP
    _settings_url_attr = "go_http_fetcher_url"

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        options = options or FetchOptions()
        settings = get_settings()
        base = getattr(settings, self._settings_url_attr, "")
        start = time.time()

        if not base:
            log.warning("go_fetcher_url_unset", tier=self.tier.value)
            return self._error(url, start, "sidecar URL not configured")

        payload = {
            "url": url,
            "options": {
                "timeout_ms": options.timeout,
                "headers": options.headers or {},
                "proxy_url": options.proxy_url or "",
                "capture_screenshot": options.capture_screenshot,
                "wait_for_selector": options.wait_for_selector or "",
                "actions": options.actions or [],
            },
        }
        try:
            async with httpx.AsyncClient(timeout=(options.timeout / 1000) + 15) as client:
                resp = await client.post(f"{base.rstrip('/')}/fetch", json=payload)
                data = resp.json()
        except Exception as exc:
            log.warning("go_fetcher_request_failed", tier=self.tier.value, url=url, error=str(exc))
            return self._error(url, start, str(exc))

        return self._to_result(url, data, start)

    def _to_result(self, url: str, data: dict, start: float) -> FetchResult:
        screenshot = data.get("screenshot_base64")
        return FetchResult(
            url=data.get("url", url),
            status_code=data.get("status_code", 0),
            html=data.get("html", "") or "",
            headers=data.get("headers", {}) or {},
            tier_used=self.tier,
            cost_usd=TIER_COSTS.get(self.tier.value, 0.0),
            duration_ms=data.get("duration_ms", int((time.time() - start) * 1000)),
            blocked=data.get("blocked", False),
            captcha_detected=data.get("captcha_detected", False),
            content_type=data.get("content_type", "text/html") or "text/html",
            screenshot_bytes=base64.b64decode(screenshot) if screenshot else None,
        )

    def _error(self, url: str, start: float, msg: str) -> FetchResult:
        return FetchResult(
            url=url,
            status_code=0,
            html="",
            tier_used=self.tier,
            cost_usd=TIER_COSTS.get(self.tier.value, 0.0),
            duration_ms=int((time.time() - start) * 1000),
            blocked=True,
            captcha_detected=False,
        )
