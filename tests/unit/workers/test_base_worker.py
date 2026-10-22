from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.scraping import FetchResult, ScrapingTier


def _make_result(
    tier: ScrapingTier = ScrapingTier.PLAYWRIGHT,
    status_code: int = 200,
    html: str = "<html>" + "x" * 300 + "</html>",
    blocked: bool = False,
    captcha: bool = False,
) -> FetchResult:
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


class TestBaseWorkerWithoutPool:
    @pytest.mark.asyncio
    async def test_fetch_defaults_to_playwright(self):
        from src.workers.base import BaseWorker

        class W(BaseWorker):
            async def execute(self, urls):
                return []

        worker = W(domain="example.com", job_id=str(uuid4()))
        mock_fetcher = MagicMock()
        mock_fetcher.fetch = AsyncMock(return_value=_make_result())

        with patch("src.workers.base.create_fetcher", return_value=mock_fetcher):
            result = await worker.fetch_page("https://example.com")

        assert result.status_code == 200


class TestBaseWorkerWithPool:
    @pytest.fixture
    def worker(self):
        from src.workers.base import BaseWorker

        class W(BaseWorker):
            async def execute(self, urls):
                return []

        return W(domain="example.com", job_id=str(uuid4()), pool=MagicMock())

    @pytest.mark.asyncio
    async def test_uses_escalation_initial_tier(self, worker):
        """fetch_page delegates to fetch_with_escalation on the escalation service."""
        expected = _make_result()
        with patch.object(
            worker._escalation,
            "fetch_with_escalation",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_fwe:
            result = await worker.fetch_page("https://example.com")
            mock_fwe.assert_called_once()
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_escalates_on_block(self, worker):
        """Escalation loop inside fetch_with_escalation tries two fetchers on block.

        Patch create_fetcher inside escalation (where the loop now lives).
        """
        blocked = _make_result(tier=ScrapingTier.PLAYWRIGHT, status_code=403, html="", blocked=True)
        success = _make_result(tier=ScrapingTier.PLAYWRIGHT_PROXY)
        call_count = 0

        async def side_effect(url, options=None):
            nonlocal call_count
            call_count += 1
            return blocked if call_count == 1 else success

        mock_fetcher = MagicMock()
        mock_fetcher.fetch = AsyncMock(side_effect=side_effect)

        with (
            patch.object(
                worker._escalation,
                "decide_initial_tier",
                new_callable=AsyncMock,
                return_value=ScrapingTier.PLAYWRIGHT,
            ),
            patch.object(
                worker._escalation, "get_next_tier",
                return_value=ScrapingTier.PLAYWRIGHT_PROXY,
            ),
            patch.object(worker._escalation, "get_escalation_wait", return_value=0),
            patch.object(worker._escalation, "record_result", new_callable=AsyncMock),
            patch("src.services.escalation.create_fetcher", return_value=mock_fetcher),
        ):
            await worker.fetch_page("https://example.com")
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_stops_at_max_tier(self, worker):
        """Returns the blocked result when no further tier is available."""
        blocked = _make_result(
            tier=ScrapingTier.PLAYWRIGHT_PROXY, status_code=403, html="", blocked=True
        )
        mock_fetcher = MagicMock()
        mock_fetcher.fetch = AsyncMock(return_value=blocked)

        with (
            patch.object(
                worker._escalation,
                "decide_initial_tier",
                new_callable=AsyncMock,
                return_value=ScrapingTier.PLAYWRIGHT_PROXY,
            ),
            patch.object(worker._escalation, "get_escalation_wait", return_value=0),
            patch.object(worker._escalation, "record_result", new_callable=AsyncMock),
            patch("src.services.escalation.create_fetcher", return_value=mock_fetcher),
        ):
            result = await worker.fetch_page("https://example.com")
            assert result.blocked is True

    @pytest.mark.asyncio
    async def test_retry_on_transport_error_succeeds_after_2_attempts(self, worker):
        """retry_async inside fetch_with_escalation retries transient errors."""
        call_count = 0

        async def fetch_side_effect(url, options=None):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TimeoutError("connection timeout")
            return _make_result()

        mock_fetcher = MagicMock()
        mock_fetcher.fetch = AsyncMock(side_effect=fetch_side_effect)

        with (
            patch.object(
                worker._escalation,
                "decide_initial_tier",
                new_callable=AsyncMock,
                return_value=ScrapingTier.PLAYWRIGHT,
            ),
            patch.object(worker._escalation, "record_result", new_callable=AsyncMock),
            patch("src.services.escalation.create_fetcher", return_value=mock_fetcher),
        ):
            await worker.fetch_page("https://example.com")
            assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_propagates_after_max_retries(self, worker):
        """TimeoutError propagates after max retries exhausted."""
        mock_fetcher = MagicMock()
        mock_fetcher.fetch = AsyncMock(side_effect=TimeoutError("connection timeout"))

        with (
            patch.object(
                worker._escalation,
                "decide_initial_tier",
                new_callable=AsyncMock,
                return_value=ScrapingTier.PLAYWRIGHT,
            ),
            patch.object(worker._escalation, "record_result", new_callable=AsyncMock),
            patch("src.services.escalation.create_fetcher", return_value=mock_fetcher),
        ):
            with pytest.raises(TimeoutError):
                await worker.fetch_page("https://example.com")
            assert mock_fetcher.fetch.call_count == 3
