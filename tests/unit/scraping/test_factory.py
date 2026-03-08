from src.models.scraping import ScrapingTier
from src.scraping.fetcher.factory import create_fetcher
from src.scraping.fetcher.lake_fetcher import LakeFetcher
from src.scraping.fetcher.lake_playwright_fetcher import LakePlaywrightFetcher
from src.scraping.fetcher.lake_playwright_proxy_fetcher import LakePlaywrightProxyFetcher
from src.scraping.fetcher.lake_proxy_fetcher import LakeProxyFetcher
from src.scraping.fetcher.lake_stealth_fetcher import LakeStealthFetcher


class TestCreateFetcher:
    def test_basic_http(self):
        assert isinstance(create_fetcher(ScrapingTier.BASIC_HTTP), LakeFetcher)

    def test_headless(self):
        assert isinstance(create_fetcher(ScrapingTier.HEADLESS_BROWSER), LakeStealthFetcher)

    def test_playwright(self):
        assert isinstance(create_fetcher(ScrapingTier.PLAYWRIGHT), LakePlaywrightFetcher)

    def test_playwright_proxy(self):
        assert isinstance(create_fetcher(ScrapingTier.PLAYWRIGHT_PROXY), LakePlaywrightProxyFetcher)

    def test_proxy(self):
        assert isinstance(create_fetcher(ScrapingTier.HEADLESS_PROXY), LakeProxyFetcher)
