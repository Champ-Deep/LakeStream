"""Unit tests for src/services/escalation.py — plan.md S3.1 / MED-1.

Covers tier-chain transitions, escalation triggers, escalation wait logic,
and initial-tier decisions for both fresh and previously-scraped domains.

The existing tests/unit/services/test_escalation.py covers a different
slice (early termination after the cron-style retry loop). This file
focuses on the pure logic functions inside EscalationService.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.scraping import FetchResult, ScrapingTier
from src.services.escalation import (
    EscalationService,
    _build_tier_order,
    _TIER_MIGRATION_MAP,
)


def _result(
    *,
    blocked: bool = False,
    captcha: bool = False,
    status_code: int = 200,
    tier: ScrapingTier = ScrapingTier.PLAYWRIGHT,
) -> FetchResult:
    return FetchResult(
        url="https://example.com",
        status_code=status_code,
        html="<html></html>",
        tier_used=tier,
        cost_usd=0.0,
        duration_ms=10,
        blocked=blocked,
        captcha_detected=captcha,
    )


# ---------------------------------------------------------------------------
# _build_tier_order — chain shape under various env configurations
# ---------------------------------------------------------------------------


class TestTierOrder:
    def test_full_chain_when_lightpanda_and_proxy_available(self):
        with patch("src.services.escalation.get_settings") as mock_settings:
            mock_settings.return_value.lightpanda_ws_url = "ws://lp:9222"
            order = _build_tier_order(proxy_available=True)
            assert order == [
                ScrapingTier.LIGHTPANDA,
                ScrapingTier.PLAYWRIGHT,
                ScrapingTier.PLAYWRIGHT_PROXY,
            ]

    def test_chain_drops_proxy_when_unavailable(self):
        with patch("src.services.escalation.get_settings") as mock_settings:
            mock_settings.return_value.lightpanda_ws_url = "ws://lp:9222"
            order = _build_tier_order(proxy_available=False)
            assert order == [ScrapingTier.LIGHTPANDA, ScrapingTier.PLAYWRIGHT]

    def test_chain_starts_at_playwright_when_no_lightpanda(self):
        with patch("src.services.escalation.get_settings") as mock_settings:
            mock_settings.return_value.lightpanda_ws_url = ""
            order = _build_tier_order(proxy_available=True)
            assert order == [ScrapingTier.PLAYWRIGHT, ScrapingTier.PLAYWRIGHT_PROXY]


# ---------------------------------------------------------------------------
# get_next_tier — escalation transitions
# ---------------------------------------------------------------------------


class TestGetNextTier:
    @pytest.fixture
    def svc(self):
        return EscalationService(pool=MagicMock())

    def test_lightpanda_escalates_to_playwright(self, svc):
        with patch("src.services.escalation.get_settings") as s:
            s.return_value.lightpanda_ws_url = "ws://lp"
            assert (
                svc.get_next_tier(ScrapingTier.LIGHTPANDA)
                == ScrapingTier.PLAYWRIGHT
            )

    def test_playwright_escalates_to_proxy(self, svc):
        with patch("src.services.escalation.get_settings") as s:
            s.return_value.lightpanda_ws_url = "ws://lp"
            assert (
                svc.get_next_tier(ScrapingTier.PLAYWRIGHT)
                == ScrapingTier.PLAYWRIGHT_PROXY
            )

    def test_proxy_is_terminal(self, svc):
        with patch("src.services.escalation.get_settings") as s:
            s.return_value.lightpanda_ws_url = "ws://lp"
            assert svc.get_next_tier(ScrapingTier.PLAYWRIGHT_PROXY) is None

    def test_no_proxy_means_playwright_is_terminal(self, svc):
        with patch("src.services.escalation.get_settings") as s:
            s.return_value.lightpanda_ws_url = "ws://lp"
            assert (
                svc.get_next_tier(ScrapingTier.PLAYWRIGHT, proxy_available=False)
                is None
            )

    def test_unknown_tier_returns_none(self, svc):
        # If somehow a tier outside the chain comes in (e.g., escalation
        # called after the chain shrunk), we should not crash — return None.
        # Using LIGHTPANDA against a chain that no longer contains it.
        with patch("src.services.escalation.get_settings") as s:
            s.return_value.lightpanda_ws_url = ""  # chain has no LIGHTPANDA
            assert svc.get_next_tier(ScrapingTier.LIGHTPANDA) is None


# ---------------------------------------------------------------------------
# should_escalate / get_escalation_reason
# ---------------------------------------------------------------------------


class TestShouldEscalate:
    @pytest.fixture
    def svc(self):
        return EscalationService(pool=MagicMock())

    def test_clean_result_no_escalation(self, svc):
        assert svc.should_escalate(_result()) is False
        assert svc.get_escalation_reason(_result()) == "none"

    def test_blocked_triggers_escalation(self, svc):
        assert svc.should_escalate(_result(blocked=True)) is True
        assert "blocked" in svc.get_escalation_reason(_result(blocked=True))

    def test_captcha_triggers_escalation(self, svc):
        assert svc.should_escalate(_result(captcha=True)) is True
        assert "captcha" in svc.get_escalation_reason(_result(captcha=True))

    def test_both_blocked_and_captcha(self, svc):
        reason = svc.get_escalation_reason(_result(blocked=True, captcha=True))
        assert "blocked" in reason and "captcha" in reason


# ---------------------------------------------------------------------------
# get_escalation_wait — rate-limit-driven waits
# ---------------------------------------------------------------------------


class TestEscalationWait:
    @pytest.fixture
    def svc(self):
        return EscalationService(pool=MagicMock())

    def test_terminal_with_proxy_waits_for_termination(self, svc):
        # next_tier=None means "we've exhausted the chain".
        # If a proxy *is* configured, we still wait before terminating
        # (the architecture used to fall back to a final retry).
        wait = svc.get_escalation_wait(
            ScrapingTier.PLAYWRIGHT_PROXY,
            next_tier=None,
            result=_result(blocked=True, status_code=429),
            proxy_available=True,
        )
        assert wait == 600  # TERMINATION_WAIT_SECONDS

    def test_terminal_without_proxy_no_wait(self, svc):
        wait = svc.get_escalation_wait(
            ScrapingTier.PLAYWRIGHT, next_tier=None, proxy_available=False
        )
        assert wait == 0

    def test_captcha_only_skips_wait(self, svc):
        # Captcha is immediate — don't sit on a 10-min timer when the
        # site has *already* shown a captcha (more time won't help).
        wait = svc.get_escalation_wait(
            ScrapingTier.LIGHTPANDA,
            next_tier=ScrapingTier.PLAYWRIGHT,
            result=_result(captcha=True, status_code=200),
        )
        assert wait == 0

    def test_non_rate_limit_escalates_immediately(self, svc):
        # A 200-status-code with empty content gets escalated immediately
        # — no point waiting for a "rate limit" that didn't happen.
        wait = svc.get_escalation_wait(
            ScrapingTier.LIGHTPANDA,
            next_tier=ScrapingTier.PLAYWRIGHT,
            result=_result(blocked=True, status_code=200),
        )
        assert wait == 0

    def test_429_triggers_lightpanda_to_playwright_wait(self, svc):
        wait = svc.get_escalation_wait(
            ScrapingTier.LIGHTPANDA,
            next_tier=ScrapingTier.PLAYWRIGHT,
            result=_result(blocked=True, status_code=429),
        )
        assert wait == 120

    def test_503_triggers_playwright_to_proxy_wait(self, svc):
        wait = svc.get_escalation_wait(
            ScrapingTier.PLAYWRIGHT,
            next_tier=ScrapingTier.PLAYWRIGHT_PROXY,
            result=_result(blocked=True, status_code=503),
        )
        assert wait == 600

    def test_no_result_uses_table_default(self, svc):
        # When result is None we don't have rate-limit info, so the
        # configured wait is used as-is.
        wait = svc.get_escalation_wait(
            ScrapingTier.PLAYWRIGHT,
            next_tier=ScrapingTier.PLAYWRIGHT_PROXY,
            result=None,
        )
        assert wait == 600


# ---------------------------------------------------------------------------
# decide_initial_tier — domain-history-aware tier selection
# ---------------------------------------------------------------------------


class TestDecideInitialTier:
    @pytest.fixture
    def svc(self):
        return EscalationService(pool=MagicMock())

    @pytest.mark.asyncio
    async def test_fresh_domain_starts_at_cheapest(self, svc):
        with (
            patch(
                "src.services.escalation.get_domain_metadata",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("src.services.escalation.get_settings") as s,
        ):
            s.return_value.lightpanda_ws_url = "ws://lp"
            tier = await svc.decide_initial_tier("fresh.example.com")
            assert tier == ScrapingTier.LIGHTPANDA

    @pytest.mark.asyncio
    async def test_prior_strategy_reused(self, svc):
        meta = MagicMock()
        meta.last_successful_strategy = "playwright_proxy"
        with (
            patch(
                "src.services.escalation.get_domain_metadata",
                new_callable=AsyncMock,
                return_value=meta,
            ),
            patch("src.services.escalation.get_settings") as s,
        ):
            s.return_value.lightpanda_ws_url = "ws://lp"
            tier = await svc.decide_initial_tier("hard.example.com")
            assert tier == ScrapingTier.PLAYWRIGHT_PROXY

    @pytest.mark.asyncio
    async def test_deprecated_tier_is_migrated(self, svc):
        # Old DBs may have last_successful_strategy = "headless_browser"
        # which is no longer a valid ScrapingTier value. Verify it maps
        # to the modern equivalent.
        assert _TIER_MIGRATION_MAP["headless_browser"] == ScrapingTier.PLAYWRIGHT

        meta = MagicMock()
        meta.last_successful_strategy = "headless_browser"
        with (
            patch(
                "src.services.escalation.get_domain_metadata",
                new_callable=AsyncMock,
                return_value=meta,
            ),
            patch("src.services.escalation.get_settings") as s,
        ):
            s.return_value.lightpanda_ws_url = "ws://lp"
            tier = await svc.decide_initial_tier("legacy.example.com")
            assert tier == ScrapingTier.PLAYWRIGHT

    @pytest.mark.asyncio
    async def test_unknown_strategy_falls_back_to_default(self, svc):
        meta = MagicMock()
        meta.last_successful_strategy = "totally_made_up_tier"
        with (
            patch(
                "src.services.escalation.get_domain_metadata",
                new_callable=AsyncMock,
                return_value=meta,
            ),
            patch("src.services.escalation.get_settings") as s,
        ):
            s.return_value.lightpanda_ws_url = "ws://lp"
            tier = await svc.decide_initial_tier("weird.example.com")
            assert tier == ScrapingTier.LIGHTPANDA  # cheapest of the chain
