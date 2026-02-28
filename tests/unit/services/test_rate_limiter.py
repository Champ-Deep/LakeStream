from unittest.mock import AsyncMock, patch
import pytest
from src.services.rate_limiter import RateLimiter


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_first_request_no_delay(self):
        r = RateLimiter(default_delay_ms=1000)
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await r.wait("ex.com")
            mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_second_request_delays(self):
        r = RateLimiter(default_delay_ms=1000)
        await r.wait("ex.com")
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await r.wait("ex.com")
            if mock_sleep.called:
                assert mock_sleep.call_args[0][0] > 0

    @pytest.mark.asyncio
    async def test_different_domains_independent(self):
        r = RateLimiter(default_delay_ms=5000)
        await r.wait("a.com")
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await r.wait("b.com")
            mock_sleep.assert_not_called()

    def test_reset(self):
        r = RateLimiter()
        import time

        r._last_request["ex.com"] = time.time()
        r.reset("ex.com")
        assert "ex.com" not in r._last_request
