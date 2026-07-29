from src.models.scraping import ScrapingTier
from src.scraping.fetcher.lake_go_browser_fetcher import LakeGoBrowserFetcher
from src.scraping.fetcher.lake_go_http_fetcher import LakeGoHttpFetcher
from src.scraping.fetcher.lake_lightpanda_fetcher import LakeLightPandaFetcher
from src.scraping.fetcher.lake_playwright_fetcher import LakePlaywrightFetcher
from src.scraping.fetcher.lake_playwright_proxy_fetcher import LakePlaywrightProxyFetcher

_FETCHERS = {
    ScrapingTier.LIGHTPANDA: LakeLightPandaFetcher,
    ScrapingTier.PLAYWRIGHT: LakePlaywrightFetcher,
    ScrapingTier.PLAYWRIGHT_PROXY: LakePlaywrightProxyFetcher,
    # Experimental Go sidecar tiers (flag: enable_go_fetchers). Only reachable
    # via explicit tier_override unless the flag routes escalation to them.
    ScrapingTier.GO_HTTP: LakeGoHttpFetcher,
    ScrapingTier.GO_BROWSER: LakeGoBrowserFetcher,
}


def create_fetcher(tier: ScrapingTier):
    """Create a fetcher instance for the given tier."""
    fetcher_class = _FETCHERS.get(tier, LakePlaywrightFetcher)
    return fetcher_class()
