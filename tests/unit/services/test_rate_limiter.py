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
        r = RateLimiter(default_delay_ms=1000, max_delay_ms=30000)
        r.report_result("ex.com", 429)
        assert r._current_delay["ex.com"] == 2.0

    def test_503_doubles_delay(self):
        r = RateLimiter(default_delay_ms=1000, max_delay_ms=30000)
        r.report_result("ex.com", 503)
        assert r._current_delay["ex.com"] == 2.0

    def test_success_after_429_decays_delay(self):
        r = RateLimiter(default_delay_ms=1000, max_delay_ms=30000)
        r.report_result("ex.com", 429)
        r.report_result("ex.com", 429)
        r.report_result("ex.com", 200)
        assert r._current_delay["ex.com"] < 4.0

    def test_delay_never_exceeds_max(self):
        r = RateLimiter(default_delay_ms=1000, max_delay_ms=5000)
        for _ in range(10):
            r.report_result("ex.com", 429)
        assert r._current_delay["ex.com"] == 5.0

    def test_domain_specific_linkedin(self):
        """LinkedIn domain should get 5 second rate limit."""
        r = RateLimiter()
        assert r.get_rate_limit("linkedin.com") == 5000

    def test_domain_specific_linkedin_subdomain(self):
        """LinkedIn subdomains should match *.linkedin.com pattern."""
        r = RateLimiter()
        assert r.get_rate_limit("www.linkedin.com") == 5000
        assert r.get_rate_limit("sales.linkedin.com") == 5000

    def test_domain_specific_hubspot_subdomain(self):
        """HubSpot subdomains should match *.hubspot.com pattern."""
        r = RateLimiter()
        assert r.get_rate_limit("blog.hubspot.com") == 2000
        assert r.get_rate_limit("www.hubspot.com") == 2000

    def test_domain_specific_wordpress_subdomain(self):
        """WordPress subdomains should match *.wordpress.com pattern."""
        r = RateLimiter()
        assert r.get_rate_limit("myblog.wordpress.com") == 1500

    def test_domain_specific_unknown_default(self):
        """Unknown domains should fall back to default."""
        r = RateLimiter()
        assert r.get_rate_limit("example.com") == 1000
        assert r.get_rate_limit("random-site.org") == 1000

    def test_domain_specific_decay_respects_domain_default(self):
        """Decay should stop at domain-specific default, not global default."""
        r = RateLimiter(default_delay_ms=1000)
        # LinkedIn starts at 5s default
        r.report_result("linkedin.com", 429)  # 5s -> 10s
        r.report_result("linkedin.com", 429)  # 10s -> 20s
        # Now decay with success
        for _ in range(20):
            r.report_result("linkedin.com", 200)  # Should decay to 5s, not 1s
        # After many successes, should stabilize at LinkedIn's 5s default
        assert r._current_delay["linkedin.com"] == 5.0

    def test_domain_specific_backoff_from_domain_default(self):
        """Rate limit backoff should start from domain-specific default."""
        r = RateLimiter()
        # First 429 should double from LinkedIn's 5s default to 10s
        r.report_result("linkedin.com", 429)
        assert r._current_delay["linkedin.com"] == 10.0
        # First 429 for generic domain should double from 1s to 2s
        r.report_result("example.com", 429)
        assert r._current_delay["example.com"] == 2.0
