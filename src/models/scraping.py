from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class ScrapingTier(StrEnum):
    LIGHTPANDA = "lightpanda"
    PLAYWRIGHT = "playwright"
    PLAYWRIGHT_PROXY = "playwright_proxy"
    GO_HTTP = "go_http"  # Go sidecar: fast HTTP fetch (experimental, flag-gated)
    GO_BROWSER = "go_browser"  # Go sidecar: headless Chrome via chromedp (experimental)


class FetchResult(BaseModel):
    url: str
    status_code: int
    html: str
    headers: dict[str, str] = {}
    tier_used: ScrapingTier
    cost_usd: float
    duration_ms: int
    blocked: bool = False
    captcha_detected: bool = False
    content_bytes: bytes | None = None  # Binary content (PDF, DOCX)
    content_type: str = "text/html"
    screenshot_bytes: bytes | None = None  # PNG when capture_screenshot requested
    content_hash: str | None = None  # sha256 of normalized markdown (cache/change)
    fetched_at: datetime | None = None


class FetchOptions(BaseModel):
    tier: ScrapingTier | None = None
    timeout: int = 30000
    wait_for_selector: str | None = None
    headers: dict[str, str] = {}
    proxy_url: str | None = None  # Org-level proxy override from settings UI
    region: str | None = None  # Geo-target: "us", "eu", "asia", etc.
    capture_screenshot: bool = False
    # Scripted browser actions, interpreted by the Playwright fetcher (Phase 3).
    # Each item: {"type": "click|scroll|wait|screenshot", "selector"?: str, "ms"?: int}
    actions: list[dict] | None = None
