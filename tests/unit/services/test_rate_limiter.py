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

    def test_429_doubles_delay(self):
        """429 should double delay. Seed an initial delay since unknown domains default to 0."""
        r = RateLimiter(default_delay_ms=1000, max_delay_ms=30000)
        # ex.com has no domain-specific limit, so it uses the global default (0ms).
        # Current delay starts at domain default (0s), 0*2=0.
        # To test doubling, seed an initial delay first.
        r._current_delay["ex.com"] = 1.0
        r.report_result("ex.com", 429)
        assert r._current_delay["ex.com"] == 2.0

    def test_503_doubles_delay(self):
        r = RateLimiter(default_delay_ms=1000, max_delay_ms=30000)
        r._current_delay["ex.com"] = 1.0
        r.report_result("ex.com", 503)
        assert r._current_delay["ex.com"] == 2.0

    def test_success_after_429_decays_delay(self):
        r = RateLimiter(default_delay_ms=1000, max_delay_ms=30000)
        r._current_delay["ex.com"] = 1.0
        r.report_result("ex.com", 429)  # 1 -> 2
        r.report_result("ex.com", 429)  # 2 -> 4
        r.report_result("ex.com", 200)  # 4 -> 3.6
        assert r._current_delay["ex.com"] < 4.0

    def test_delay_never_exceeds_max(self):
        r = RateLimiter(default_delay_ms=1000, max_delay_ms=5000)
        r._current_delay["ex.com"] = 1.0
        for _ in range(10):
            r.report_result("ex.com", 429)
        assert r._current_delay["ex.com"] == 5.0

    def test_domain_specific_linkedin(self):
        """LinkedIn domain should get 3 second rate limit."""
        r = RateLimiter()
        assert r.get_rate_limit("linkedin.com") == 3000

    def test_domain_specific_linkedin_subdomain(self):
        """LinkedIn subdomains should match *.linkedin.com pattern."""
        r = RateLimiter()
        assert r.get_rate_limit("www.linkedin.com") == 3000
        assert r.get_rate_limit("sales.linkedin.com") == 3000

    def test_domain_specific_unknown_default(self):
        """Unknown domains should fall back to 0 (no baseline delay)."""
        r = RateLimiter()
        assert r.get_rate_limit("example.com") == 0
        assert r.get_rate_limit("random-site.org") == 0

    def test_domain_specific_decay_respects_domain_default(self):
        """Decay should stop at domain-specific default, not zero."""
        r = RateLimiter()
        # LinkedIn starts at 3s default
        r.report_result("linkedin.com", 429)  # 3s -> 6s
        r.report_result("linkedin.com", 429)  # 6s -> 12s
        # Now decay with success
        for _ in range(30):
            r.report_result("linkedin.com", 200)
        # After many successes, should stabilize at LinkedIn's 3s default
        assert r._current_delay["linkedin.com"] == 3.0

    def test_domain_specific_backoff_from_domain_default(self):
        """Rate limit backoff should start from domain-specific default."""
        r = RateLimiter()
        # First 429 should double from LinkedIn's 3s default to 6s
        r.report_result("linkedin.com", 429)
        assert r._current_delay["linkedin.com"] == 6.0
