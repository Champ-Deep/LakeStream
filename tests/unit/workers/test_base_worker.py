from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest
from src.models.scraping import FetchResult, ScrapingTier


def _make_result(
    tier: ScrapingTier = ScrapingTier.BASIC_HTTP,
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
    async def test_fetch_defaults_to_basic_http(self):
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
        with (
            patch.object(
                worker._escalation,
                "decide_initial_tier",
                new_callable=AsyncMock,
                return_value=ScrapingTier.BASIC_HTTP,
            ),
            patch.object(worker._escalation, "should_escalate", return_value=False),
            patch.object(worker._escalation, "record_result", new_callable=AsyncMock),
            patch("src.workers.base.create_fetcher") as mock_factory,
        ):
            mock_fetcher = MagicMock()
            mock_fetcher.fetch = AsyncMock(return_value=_make_result())
            mock_factory.return_value = mock_fetcher
            result = await worker.fetch_page("https://example.com")
            worker._escalation.decide_initial_tier.assert_called_once_with("example.com")
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_escalates_on_block(self, worker):
        blocked = _make_result(tier=ScrapingTier.BASIC_HTTP, status_code=403, html="", blocked=True)
        success = _make_result(tier=ScrapingTier.HEADLESS_BROWSER)
        call_count = 0

        async def side_effect(url, options=None):
            nonlocal call_count
            call_count += 1
            return blocked if call_count == 1 else success

        with (
            patch.object(
                worker._escalation,
                "decide_initial_tier",
                new_callable=AsyncMock,
                return_value=ScrapingTier.BASIC_HTTP,
            ),
            patch.object(worker._escalation, "should_escalate", side_effect=[True, False]),
            patch.object(
                worker._escalation, "get_next_tier", return_value=ScrapingTier.HEADLESS_BROWSER
            ),
            patch.object(worker._escalation, "record_result", new_callable=AsyncMock),
            patch("src.workers.base.create_fetcher") as mock_factory,
        ):
            mock_fetcher = MagicMock()
            mock_fetcher.fetch = AsyncMock(side_effect=side_effect)
            mock_factory.return_value = mock_fetcher
            result = await worker.fetch_page("https://example.com")
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_stops_at_max_tier(self, worker):
        blocked = _make_result(
            tier=ScrapingTier.HEADLESS_PROXY, status_code=403, html="", blocked=True
        )
        with (
            patch.object(
                worker._escalation,
                "decide_initial_tier",
                new_callable=AsyncMock,
                return_value=ScrapingTier.HEADLESS_PROXY,
            ),
            patch.object(worker._escalation, "should_escalate", return_value=True),
            patch.object(worker._escalation, "get_next_tier", return_value=None),
            patch.object(worker._escalation, "record_result", new_callable=AsyncMock),
            patch("src.workers.base.create_fetcher") as mock_factory,
        ):
            mock_fetcher = MagicMock()
            mock_fetcher.fetch = AsyncMock(return_value=blocked)
            mock_factory.return_value = mock_fetcher
            result = await worker.fetch_page("https://example.com")
            assert result.blocked is True

    @pytest.mark.asyncio
    async def test_records_cost(self, worker):
        with (
            patch.object(
                worker._escalation,
                "decide_initial_tier",
                new_callable=AsyncMock,
                return_value=ScrapingTier.BASIC_HTTP,
            ),
            patch.object(worker._escalation, "should_escalate", return_value=False),
            patch.object(worker._escalation, "record_result", new_callable=AsyncMock),
            patch("src.workers.base.create_fetcher") as mock_factory,
        ):
            mock_fetcher = MagicMock()
            mock_fetcher.fetch = AsyncMock(return_value=_make_result())
            mock_factory.return_value = mock_fetcher
            await worker.fetch_page("https://example.com")
            assert worker._cost_tracker.get_job_cost(worker.job_id) > 0

    @pytest.mark.asyncio
    async def test_stops_when_budget_exceeded(self, worker):
        blocked = _make_result(status_code=403, html="", blocked=True)
        with (
            patch.object(
                worker._escalation,
                "decide_initial_tier",
                new_callable=AsyncMock,
                return_value=ScrapingTier.BASIC_HTTP,
            ),
            patch.object(worker._escalation, "should_escalate", return_value=True),
            patch.object(
                worker._escalation, "get_next_tier", return_value=ScrapingTier.HEADLESS_BROWSER
            ),
            patch.object(worker._escalation, "record_result", new_callable=AsyncMock),
            patch.object(worker._cost_tracker, "check_budget", return_value=False),
            patch("src.workers.base.create_fetcher") as mock_factory,
        ):
            mock_fetcher = MagicMock()
            mock_fetcher.fetch = AsyncMock(return_value=blocked)
            mock_factory.return_value = mock_fetcher
            result = await worker.fetch_page("https://example.com")
            assert result.blocked is True
            assert mock_fetcher.fetch.call_count == 1
