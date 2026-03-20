from src.models.scraping import ScrapingTier
from src.scraping.fetcher.lake_lightpanda_fetcher import LakeLightPandaFetcher
from src.scraping.fetcher.lake_playwright_fetcher import LakePlaywrightFetcher
from src.scraping.fetcher.lake_playwright_proxy_fetcher import LakePlaywrightProxyFetcher

_FETCHERS = {
    ScrapingTier.LIGHTPANDA: LakeLightPandaFetcher,
    ScrapingTier.PLAYWRIGHT: LakePlaywrightFetcher,
    ScrapingTier.PLAYWRIGHT_PROXY: LakePlaywrightProxyFetcher,
}


def create_fetcher(tier: ScrapingTier):
    """Create a fetcher instance for the given tier.

    Tier order: LIGHTPANDA → PLAYWRIGHT → PLAYWRIGHT_PROXY
    """
    fetcher_class = _FETCHERS.get(tier, LakePlaywrightFetcher)
    return fetcher_class()
