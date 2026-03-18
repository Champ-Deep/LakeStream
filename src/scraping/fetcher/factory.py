from src.models.scraping import ScrapingTier
from src.scraping.fetcher.lake_fetcher import LakeFetcher
from src.scraping.fetcher.lake_lightpanda_fetcher import LakeLightpandaFetcher
from src.scraping.fetcher.lake_playwright_fetcher import LakePlaywrightFetcher
from src.scraping.fetcher.lake_playwright_proxy_fetcher import LakePlaywrightProxyFetcher
from src.scraping.fetcher.lake_proxy_fetcher import LakeProxyFetcher
from src.scraping.fetcher.lake_stealth_fetcher import LakeStealthFetcher

_FETCHERS = {
    ScrapingTier.LIGHTPANDA: LakeLightpandaFetcher,
    ScrapingTier.BASIC_HTTP: LakeFetcher,
    ScrapingTier.HEADLESS_BROWSER: LakeStealthFetcher,
    ScrapingTier.PLAYWRIGHT: LakePlaywrightFetcher,
    ScrapingTier.PLAYWRIGHT_PROXY: LakePlaywrightProxyFetcher,
    ScrapingTier.HEADLESS_PROXY: LakeProxyFetcher,
}


def create_fetcher(
    tier: ScrapingTier,
) -> LakeLightpandaFetcher | LakeFetcher | LakeStealthFetcher | LakePlaywrightFetcher | LakePlaywrightProxyFetcher | LakeProxyFetcher:
    """Create a fetcher instance for the given tier."""
    fetcher_class = _FETCHERS.get(tier, LakeLightpandaFetcher)
    return fetcher_class()
