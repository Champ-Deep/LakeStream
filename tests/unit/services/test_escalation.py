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
        meta.last_successful_strategy = "playwright"
        with patch(
            "src.services.escalation.get_domain_metadata", new_callable=AsyncMock, return_value=meta
        ):
            assert await svc.decide_initial_tier("known.com") == ScrapingTier.PLAYWRIGHT

    @pytest.mark.asyncio
    async def test_tier_migration_headless_browser(self, svc):
        """Test deprecated headless_browser migrates to PLAYWRIGHT."""
        meta = MagicMock()
        meta.last_successful_strategy = "headless_browser"
        with patch(
            "src.services.escalation.get_domain_metadata", new_callable=AsyncMock, return_value=meta
        ):
            result = await svc.decide_initial_tier("known.com")
            assert result == ScrapingTier.PLAYWRIGHT

    @pytest.mark.asyncio
    async def test_tier_migration_headless_proxy(self, svc):
        """Test deprecated headless_proxy migrates to PLAYWRIGHT_PROXY."""
        meta = MagicMock()
        meta.last_successful_strategy = "headless_proxy"
        with patch(
            "src.services.escalation.get_domain_metadata", new_callable=AsyncMock, return_value=meta
        ):
            result = await svc.decide_initial_tier("known.com")
            assert result == ScrapingTier.PLAYWRIGHT_PROXY

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
        # 3-tier system: BASIC_HTTP → PLAYWRIGHT
        assert svc.get_next_tier(ScrapingTier.BASIC_HTTP) == ScrapingTier.PLAYWRIGHT

    def test_next_tier_playwright(self, svc):
        # 3-tier system: PLAYWRIGHT → PLAYWRIGHT_PROXY
        assert svc.get_next_tier(ScrapingTier.PLAYWRIGHT) == ScrapingTier.PLAYWRIGHT_PROXY

    def test_next_tier_playwright_proxy_none(self, svc):
        # 3-tier system: PLAYWRIGHT_PROXY is final tier
        assert svc.get_next_tier(ScrapingTier.PLAYWRIGHT_PROXY) is None

    def test_next_tier_deprecated_headless(self, svc):
        # Deprecated tier (not in tier order) → None
        assert svc.get_next_tier(ScrapingTier.HEADLESS_BROWSER) is None

    def test_next_tier_deprecated_proxy(self, svc):
        # Deprecated tier (not in tier order) → None
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

    def test_next_tier_playwright_no_proxy_blocks(self, svc):
        """Escalation from PLAYWRIGHT should be blocked when no proxy is available."""
        assert svc.get_next_tier(ScrapingTier.PLAYWRIGHT, proxy_available=False) is None

    def test_next_tier_playwright_with_proxy_allows(self, svc):
        """Escalation from PLAYWRIGHT should proceed when proxy is available."""
        assert svc.get_next_tier(ScrapingTier.PLAYWRIGHT, proxy_available=True) == ScrapingTier.PLAYWRIGHT_PROXY

    def test_next_tier_basic_unaffected_by_proxy_flag(self, svc):
        """Escalation from BASIC_HTTP to PLAYWRIGHT is unaffected by proxy flag."""
        assert svc.get_next_tier(ScrapingTier.BASIC_HTTP, proxy_available=False) == ScrapingTier.PLAYWRIGHT

    @pytest.mark.asyncio
    async def test_linkedin_healthy_session_uses_playwright(self, svc):
        """LinkedIn with healthy session (<50 requests) should use PLAYWRIGHT (no proxy)."""
        session_data = {
            "storage_state": {},
            "created_at": 1234567890.0,
            "last_used_at": 1234567890.0,
            "request_count": 10,
            "authenticated": True,
        }

        with patch.object(svc, "_check_session_health", new_callable=AsyncMock) as mock_health:
            with patch(
                "src.services.escalation.get_domain_metadata",
                new_callable=AsyncMock,
                return_value=None,
            ):
                mock_health.return_value = session_data
                tier = await svc.decide_initial_tier("linkedin.com")
                assert tier == ScrapingTier.PLAYWRIGHT

    @pytest.mark.asyncio
    async def test_linkedin_aging_session_uses_proxy(self, svc):
        """LinkedIn with aging session (>=50 requests) should use PLAYWRIGHT_PROXY preemptively."""
        session_data = {
            "storage_state": {},
            "created_at": 1234567890.0,
            "last_used_at": 1234567890.0,
            "request_count": 75,
            "authenticated": True,
        }

        with patch.object(svc, "_check_session_health", new_callable=AsyncMock) as mock_health:
            with patch(
                "src.services.escalation.get_domain_metadata",
                new_callable=AsyncMock,
                return_value=None,
            ):
                mock_health.return_value = session_data
                tier = await svc.decide_initial_tier("linkedin.com")
                assert tier == ScrapingTier.PLAYWRIGHT_PROXY

    @pytest.mark.asyncio
    async def test_linkedin_no_session_falls_back_to_normal(self, svc):
        """LinkedIn with no session should fall back to normal tier selection."""
        with patch.object(svc, "_check_session_health", new_callable=AsyncMock) as mock_health:
            with patch(
                "src.services.escalation.get_domain_metadata",
                new_callable=AsyncMock,
                return_value=None,
            ):
                mock_health.return_value = None
                tier = await svc.decide_initial_tier("linkedin.com")
                assert tier == ScrapingTier.BASIC_HTTP

    @pytest.mark.asyncio
    async def test_linkedin_unauthenticated_session_falls_back(self, svc):
        """LinkedIn with unauthenticated session should fall back to normal tier selection."""
        session_data = {
            "storage_state": {},
            "created_at": 1234567890.0,
            "last_used_at": 1234567890.0,
            "request_count": 10,
            "authenticated": False,  # Not authenticated
        }

        with patch.object(svc, "_check_session_health", new_callable=AsyncMock) as mock_health:
            with patch(
                "src.services.escalation.get_domain_metadata",
                new_callable=AsyncMock,
                return_value=None,
            ):
                mock_health.return_value = session_data
                tier = await svc.decide_initial_tier("linkedin.com")
                assert tier == ScrapingTier.BASIC_HTTP

    @pytest.mark.asyncio
    async def test_linkedin_subdomain_session_health(self, svc):
        """LinkedIn subdomains should also use session health tracking."""
        session_data = {
            "storage_state": {},
            "created_at": 1234567890.0,
            "last_used_at": 1234567890.0,
            "request_count": 25,
            "authenticated": True,
        }

        with patch.object(svc, "_check_session_health", new_callable=AsyncMock) as mock_health:
            with patch(
                "src.services.escalation.get_domain_metadata",
                new_callable=AsyncMock,
                return_value=None,
            ):
                mock_health.return_value = session_data
                # Test various LinkedIn subdomains
                tier = await svc.decide_initial_tier("www.linkedin.com")
                assert tier == ScrapingTier.PLAYWRIGHT

                tier = await svc.decide_initial_tier("sales.linkedin.com")
                assert tier == ScrapingTier.PLAYWRIGHT

    @pytest.mark.asyncio
    async def test_non_linkedin_domain_skips_session_health(self, svc):
        """Non-LinkedIn domains should skip session health check entirely."""
        with patch.object(svc, "_check_session_health", new_callable=AsyncMock) as mock_health:
            with patch(
                "src.services.escalation.get_domain_metadata",
                new_callable=AsyncMock,
                return_value=None,
            ):
                tier = await svc.decide_initial_tier("example.com")
                # Session health should not be called for non-LinkedIn domains
                mock_health.assert_not_called()
                assert tier == ScrapingTier.BASIC_HTTP
