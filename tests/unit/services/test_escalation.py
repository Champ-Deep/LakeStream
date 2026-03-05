from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.scraping import FetchResult, ScrapingTier
from src.services.escalation import EscalationService


def _result(
    tier=ScrapingTier.BASIC_HTTP, status_code=200, html="x" * 300, blocked=False, captcha=False
):
    return FetchResult(
        url="https://example.com",
        status_code=status_code,
        html=html,
        headers={},
        tier_used=tier,
        cost_usd=0.0001,
        duration_ms=100,
        blocked=blocked,
        captcha_detected=captcha,
    )


class TestEscalationService:
    @pytest.fixture
    def svc(self):
        return EscalationService(MagicMock())

    @pytest.mark.asyncio
    async def test_initial_tier_no_history(self, svc):
        with patch(
            "src.services.escalation.get_domain_metadata", new_callable=AsyncMock, return_value=None
        ):
            assert await svc.decide_initial_tier("new.com") == ScrapingTier.BASIC_HTTP

    @pytest.mark.asyncio
    async def test_initial_tier_from_history(self, svc):
        meta = MagicMock()
        meta.last_successful_strategy = "headless_browser"
        with patch(
            "src.services.escalation.get_domain_metadata", new_callable=AsyncMock, return_value=meta
        ):
            assert await svc.decide_initial_tier("known.com") == ScrapingTier.HEADLESS_BROWSER

    def test_should_escalate_blocked(self, svc):
        assert svc.should_escalate(_result(blocked=True)) is True

    def test_should_escalate_captcha(self, svc):
        assert svc.should_escalate(_result(captcha=True)) is True

    def test_should_escalate_403(self, svc):
        # Fetchers set blocked=True for 403, escalation checks the flag
        assert svc.should_escalate(_result(status_code=403, blocked=True)) is True

    def test_should_escalate_429(self, svc):
        # Fetchers set blocked=True for 429, escalation checks the flag
        assert svc.should_escalate(_result(status_code=429, blocked=True)) is True

    def test_should_escalate_tiny_200(self, svc):
        # Fetchers set blocked=True for tiny HTML, escalation checks the flag
        assert svc.should_escalate(_result(status_code=200, html="tiny", blocked=True)) is True

    def test_should_not_escalate_success(self, svc):
        assert svc.should_escalate(_result()) is False

    def test_next_tier_basic(self, svc):
        assert svc.get_next_tier(ScrapingTier.BASIC_HTTP) == ScrapingTier.HEADLESS_BROWSER

    def test_next_tier_headless(self, svc):
        assert svc.get_next_tier(ScrapingTier.HEADLESS_BROWSER) == ScrapingTier.HEADLESS_PROXY

    def test_next_tier_proxy_none(self, svc):
        assert svc.get_next_tier(ScrapingTier.HEADLESS_PROXY) is None

    @pytest.mark.asyncio
    async def test_record_result_success(self, svc):
        with patch(
            "src.services.escalation.upsert_domain_metadata", new_callable=AsyncMock
        ) as mock:
            await svc.record_result(
                "ex.com", _result(tier=ScrapingTier.HEADLESS_BROWSER), success=True
            )
            assert mock.call_args[1]["last_successful_strategy"] == "headless_browser"

    @pytest.mark.asyncio
    async def test_record_result_failure(self, svc):
        with patch(
            "src.services.escalation.upsert_domain_metadata", new_callable=AsyncMock
        ) as mock:
            await svc.record_result("ex.com", _result(blocked=True), success=False)
            assert mock.call_args[1]["last_successful_strategy"] is None
