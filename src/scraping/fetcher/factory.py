from src.models.scraping import ScrapingTier
from src.scraping.fetcher.scrapling_fetcher import ScraplingFetcher
from src.scraping.fetcher.scrapling_proxy_fetcher import ScraplingProxyFetcher
from src.scraping.fetcher.scrapling_stealth_fetcher import ScraplingStealthFetcher

_FETCHERS = {
    ScrapingTier.BASIC_HTTP: ScraplingFetcher,
    ScrapingTier.HEADLESS_BROWSER: ScraplingStealthFetcher,
    ScrapingTier.HEADLESS_PROXY: ScraplingProxyFetcher,
}


def create_fetcher(
    tier: ScrapingTier,
) -> ScraplingFetcher | ScraplingStealthFetcher | ScraplingProxyFetcher:
    """Create a fetcher instance for the given tier."""
    fetcher_class = _FETCHERS.get(tier, ScraplingFetcher)
    return fetcher_class()
