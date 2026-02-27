from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.scraping import FetchResult, ScrapingTier
from src.scraping.fetcher.factory import create_fetcher
from src.services.escalation import EscalationService


class MockResponse:
    """Mock response object."""

    def __init__(self, html_content: str = "", status: int = 200):
        self.html_content = html_content
        self.status = status


class TestLakeEscalationIntegration:
    """Integration tests for escalation with Lake fetchers."""

    def test_factory_creates_correct_fetcher_tier(self):
        """Test that factory creates correct fetcher for each tier."""
        fetcher_basic = create_fetcher(ScrapingTier.BASIC_HTTP)
        assert fetcher_basic.__class__.__name__ == "LakeFetcher"

        fetcher_headless = create_fetcher(ScrapingTier.HEADLESS_BROWSER)
        assert fetcher_headless.__class__.__name__ == "LakeStealthFetcher"

        fetcher_proxy = create_fetcher(ScrapingTier.HEADLESS_PROXY)
        assert fetcher_proxy.__class__.__name__ == "LakeProxyFetcher"

    def test_factory_default_fallback(self):
        """Test that factory falls back to LakeFetcher for unknown tiers."""
        fetcher = create_fetcher(ScrapingTier.BASIC_HTTP)
        assert fetcher.__class__.__name__ == "LakeFetcher"

    @pytest.mark.asyncio
    async def test_escalation_service_uses_lake_fetcher(self):
        """Test that escalation service properly works with Lake fetchers."""
        mock_pool = MagicMock()

        escalation = EscalationService(mock_pool)

        result = FetchResult(
            url="https://example.com",
            status_code=200,
            html="<html><head><title>Test Page</title></head><body><div>Content here with enough text to exceed 200 characters minimum requirement for blocked detection logic. Adding more text here to make sure it passes.</div></body></html>",
            headers={},
            tier_used=ScrapingTier.BASIC_HTTP,
            cost_usd=0.0001,
            duration_ms=100,
            blocked=False,
            captcha_detected=False,
        )

        assert escalation.should_escalate(result) is False

        blocked_result = FetchResult(
            url="https://example.com",
            status_code=403,
            html="",
            headers={},
            tier_used=ScrapingTier.BASIC_HTTP,
            cost_usd=0.0001,
            duration_ms=100,
            blocked=True,
            captcha_detected=False,
        )

        assert escalation.should_escalate(blocked_result) is True

    def test_escalation_tier_progression(self):
        """Test tier escalation progression logic."""
        mock_pool = MagicMock()
        escalation = EscalationService(mock_pool)

        next_tier = escalation.get_next_tier(ScrapingTier.BASIC_HTTP)
        assert next_tier == ScrapingTier.HEADLESS_BROWSER

        next_tier = escalation.get_next_tier(ScrapingTier.HEADLESS_BROWSER)
        assert next_tier == ScrapingTier.HEADLESS_PROXY

        next_tier = escalation.get_next_tier(ScrapingTier.HEADLESS_PROXY)
        assert next_tier is None

    @pytest.mark.asyncio
    async def test_full_escalation_chain(self):
        """Test complete escalation chain with mocked responses."""
        mock_pool = MagicMock()
        escalation = EscalationService(mock_pool)

        mock_response_blocked = MockResponse(html_content="", status=403)
        blocked_result = FetchResult(
            url="https://example.com",
            status_code=403,
            html="",
            headers={},
            tier_used=ScrapingTier.BASIC_HTTP,
            cost_usd=0.0001,
            duration_ms=100,
            blocked=True,
            captcha_detected=False,
        )

        assert escalation.should_escalate(blocked_result) is True
        next_tier = escalation.get_next_tier(blocked_result.tier_used)
        assert next_tier == ScrapingTier.HEADLESS_BROWSER

        mock_response_captcha = MockResponse(
            html_content='<html><body><div class="g-recaptcha"></div></body></html>',
            status=200,
        )
        captcha_result = FetchResult(
            url="https://example.com",
            status_code=200,
            html='<html><body><div class="g-recaptcha"></div></body></html>',
            headers={},
            tier_used=ScrapingTier.HEADLESS_BROWSER,
            cost_usd=0.002,
            duration_ms=2000,
            blocked=False,
            captcha_detected=True,
        )

        assert escalation.should_escalate(captcha_result) is True
        next_tier = escalation.get_next_tier(captcha_result.tier_used)
        assert next_tier == ScrapingTier.HEADLESS_PROXY

        success_result = FetchResult(
            url="https://example.com",
            status_code=200,
            html="<html><head><title>Success Page</title></head><body><div>Content here with enough text to exceed 200 characters minimum requirement for blocked detection logic. This is a successful scrape result.</div></body></html>",
            headers={},
            tier_used=ScrapingTier.HEADLESS_PROXY,
            cost_usd=0.004,
            duration_ms=3000,
            blocked=False,
            captcha_detected=False,
        )

        assert escalation.should_escalate(success_result) is False
        next_tier = escalation.get_next_tier(success_result.tier_used)
        assert next_tier is None
