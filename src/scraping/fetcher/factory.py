from src.models.scraping import ScrapingTier
from src.scraping.fetcher.lake_lightpanda_fetcher import LakeLightPandaFetcher
from src.scraping.fetcher.lake_playwright_fetcher import LakePlaywrightFetcher
from src.scraping.fetcher.lake_playwright_proxy_fetcher import LakePlaywrightProxyFetcher
from src.scraping.fetcher.lake_proxy_fetcher import LakeProxyFetcher
from src.scraping.fetcher.lake_stealth_fetcher import LakeStealthFetcher

_FETCHERS = {
    ScrapingTier.LIGHTPANDA: LakeLightPandaFetcher,
    ScrapingTier.HEADLESS_BROWSER: LakeStealthFetcher,
    ScrapingTier.PLAYWRIGHT: LakePlaywrightFetcher,
    ScrapingTier.PLAYWRIGHT_PROXY: LakePlaywrightProxyFetcher,
    ScrapingTier.HEADLESS_PROXY: LakeProxyFetcher,
}


def create_fetcher(
    tier: ScrapingTier,
) -> LakeLightPandaFetcher | LakeStealthFetcher | LakePlaywrightFetcher | LakePlaywrightProxyFetcher | LakeProxyFetcher:
    """Create a fetcher instance for the given tier.

    Tier order: LIGHTPANDA → PLAYWRIGHT → PLAYWRIGHT_PROXY
    BASIC_HTTP is aliased to LIGHTPANDA for backward compatibility.
    """
    if tier == ScrapingTier.BASIC_HTTP:
        tier = ScrapingTier.LIGHTPANDA

    fetcher_class = _FETCHERS.get(tier, LakeLightPandaFetcher)
    return fetcher_class()
