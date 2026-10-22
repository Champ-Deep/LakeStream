"""Go headless-browser fetcher tier — delegates to the go-browser-fetcher sidecar.

Experimental (flag: enable_go_fetchers). Renders JavaScript, supports
screenshots + scripted actions, and applies light stealth. Shares the JSON→
FetchResult mapping with the Go HTTP fetcher.
"""

from src.models.scraping import ScrapingTier
from src.scraping.fetcher.lake_go_http_fetcher import LakeGoHttpFetcher


class LakeGoBrowserFetcher(LakeGoHttpFetcher):
    """Tier: headless Chrome via the Go sidecar (chromedp)."""

    tier = ScrapingTier.GO_BROWSER
    _settings_url_attr = "go_browser_fetcher_url"
