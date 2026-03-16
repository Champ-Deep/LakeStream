from src.models.scraping import ScrapingTier
from src.scraping.fetcher.lake_playwright_fetcher import LakePlaywrightFetcher
from src.scraping.fetcher.lake_playwright_proxy_fetcher import LakePlaywrightProxyFetcher
from src.scraping.fetcher.lake_proxy_fetcher import LakeProxyFetcher
from src.scraping.fetcher.lake_stealth_fetcher import LakeStealthFetcher

_FETCHERS = {
    ScrapingTier.BASIC_HTTP: LakePlaywrightFetcher,  # Legacy compat
    ScrapingTier.HEADLESS_BROWSER: LakeStealthFetcher,  # Deprecated
    ScrapingTier.PLAYWRIGHT: LakePlaywrightFetcher,
    ScrapingTier.PLAYWRIGHT_PROXY: LakePlaywrightProxyFetcher,
    ScrapingTier.HEADLESS_PROXY: LakeProxyFetcher,  # Deprecated
}


def create_fetcher(tier: ScrapingTier):
    """Create a fetcher instance for the given tier."""
    fetcher_class = _FETCHERS.get(tier, LakePlaywrightFetcher)
    return fetcher_class()
