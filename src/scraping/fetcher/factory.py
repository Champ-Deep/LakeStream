from src.models.scraping import ScrapingTier
from src.scraping.fetcher.lake_fetcher import LakeFetcher
from src.scraping.fetcher.lake_proxy_fetcher import LakeProxyFetcher
from src.scraping.fetcher.lake_stealth_fetcher import LakeStealthFetcher

_FETCHERS = {
    ScrapingTier.BASIC_HTTP: LakeFetcher,
    ScrapingTier.HEADLESS_BROWSER: LakeStealthFetcher,
    ScrapingTier.HEADLESS_PROXY: LakeProxyFetcher,
}


def create_fetcher(
    tier: ScrapingTier,
) -> LakeFetcher | LakeStealthFetcher | LakeProxyFetcher:
    """Create a fetcher instance for the given tier."""
    fetcher_class = _FETCHERS.get(tier, LakeFetcher)
    return fetcher_class()
