from src.models.scraping import ScrapingTier
from src.scraping.fetcher.factory import create_fetcher
from src.scraping.fetcher.lake_fetcher import LakeFetcher
from src.scraping.fetcher.lake_proxy_fetcher import LakeProxyFetcher
from src.scraping.fetcher.lake_stealth_fetcher import LakeStealthFetcher


class TestCreateFetcher:
    def test_basic_http(self):
        assert isinstance(create_fetcher(ScrapingTier.BASIC_HTTP), LakeFetcher)

    def test_headless(self):
        assert isinstance(create_fetcher(ScrapingTier.HEADLESS_BROWSER), LakeStealthFetcher)

    def test_proxy(self):
        assert isinstance(create_fetcher(ScrapingTier.HEADLESS_PROXY), LakeProxyFetcher)
