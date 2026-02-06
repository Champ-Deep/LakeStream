from src.models.scraping import ScrapingTier
from src.scraping.fetcher.browser_fetcher import BrowserFetcher
from src.scraping.fetcher.http_fetcher import HttpFetcher
from src.scraping.fetcher.proxy_fetcher import ProxyFetcher

_FETCHERS = {
    ScrapingTier.BASIC_HTTP: HttpFetcher,
    ScrapingTier.HEADLESS_BROWSER: BrowserFetcher,
    ScrapingTier.HEADLESS_PROXY: ProxyFetcher,
}


def create_fetcher(tier: ScrapingTier) -> HttpFetcher | BrowserFetcher | ProxyFetcher:
    """Create a fetcher instance for the given tier."""
    fetcher_class = _FETCHERS.get(tier, HttpFetcher)
    return fetcher_class()
