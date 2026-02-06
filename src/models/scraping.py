from enum import StrEnum

from pydantic import BaseModel


class ScrapingTier(StrEnum):
    BASIC_HTTP = "basic_http"
    HEADLESS_BROWSER = "headless_browser"
    HEADLESS_PROXY = "headless_proxy"


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


class FetchOptions(BaseModel):
    tier: ScrapingTier | None = None
    timeout: int = 30000
    wait_for_selector: str | None = None
    headers: dict[str, str] = {}
