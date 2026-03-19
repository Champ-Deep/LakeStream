from src.models.scraping import ScrapingTier
from src.scraping.fetcher.factory import create_fetcher
from src.scraping.fetcher.lake_playwright_fetcher import LakePlaywrightFetcher
from src.scraping.fetcher.lake_playwright_proxy_fetcher import LakePlaywrightProxyFetcher


class TestCreateFetcher:
    def test_playwright(self):
        assert isinstance(create_fetcher(ScrapingTier.PLAYWRIGHT), LakePlaywrightFetcher)

    def test_playwright_proxy(self):
        assert isinstance(create_fetcher(ScrapingTier.PLAYWRIGHT_PROXY), LakePlaywrightProxyFetcher)
