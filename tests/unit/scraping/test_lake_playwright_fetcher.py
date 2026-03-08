from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.scraping import ScrapingTier


class TestLakePlaywrightFetcher:
    """Tests for LakePlaywrightFetcher (Tier 2.5)."""

    @pytest.fixture
    def fetcher(self):
        from src.scraping.fetcher.lake_playwright_fetcher import LakePlaywrightFetcher

        return LakePlaywrightFetcher()

    @pytest.fixture
    def mock_playwright_context(self):
        """Create mock Playwright async_playwright context manager."""
        mock_page = AsyncMock()
        mock_page.goto = AsyncMock()
        mock_page.content = AsyncMock(return_value="<html><body>Test content</body></html>")

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.storage_state = AsyncMock(return_value={"cookies": [], "origins": []})

        mock_browser = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.close = AsyncMock()

        mock_playwright = MagicMock()
        mock_playwright.chromium = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)

        return {
            "playwright": mock_playwright,
            "browser": mock_browser,
            "context": mock_context,
            "page": mock_page,
        }

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)  # No session by default
        mock_redis.set = AsyncMock()
        return mock_redis

    async def test_fetch_creates_new_session_when_none_exists(self, fetcher, mock_playwright_context, mock_redis):
        """Test fetching creates a new session when none exists in Redis."""
        mock_page = mock_playwright_context["page"]
        mock_response = MagicMock()
        mock_response.status = 200
        mock_page.goto.return_value = mock_response

        with patch("src.scraping.fetcher.lake_playwright_fetcher.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__.return_value = mock_playwright_context["playwright"]

            with patch("src.scraping.fetcher.lake_playwright_fetcher.redis.from_url", return_value=mock_redis):
                with patch("src.scraping.fetcher.lake_playwright_fetcher.get_settings") as mock_settings:
                    mock_settings.return_value.playwright_headless = True
                    mock_settings.return_value.playwright_timeout_ms = 30000
                    mock_settings.return_value.playwright_session_ttl_seconds = 3600
                    mock_settings.return_value.min_html_bytes = 20
                    mock_settings.return_value.redis_url = "redis://localhost:6379"

                    result = await fetcher.fetch("https://example.com/test")

                    # Verify new session created
                    assert result.tier_used == ScrapingTier.PLAYWRIGHT
                    assert result.status_code == 200
                    assert result.blocked is False
                    assert result.cost_usd == 0.003
                    assert len(result.html) > 0

                    # Verify session saved to Redis
                    mock_redis.set.assert_called_once()
                    call_args = mock_redis.set.call_args
                    assert call_args[0][0] == "playwright_session:example.com"

    async def test_fetch_loads_existing_session_from_redis(self, fetcher, mock_playwright_context, mock_redis):
        """Test fetching loads and reuses existing session from Redis."""
        import json

        # Mock existing session in Redis
        existing_session = {
            "storage_state": {"cookies": [{"name": "test", "value": "123"}], "origins": []},
            "created_at": 1234567890.0,
            "last_used_at": 1234567890.0,
            "request_count": 5,
            "authenticated": True,
        }
        mock_redis.get.return_value = json.dumps(existing_session).encode()

        mock_page = mock_playwright_context["page"]
        mock_response = MagicMock()
        mock_response.status = 200
        mock_page.goto.return_value = mock_response

        with patch("src.scraping.fetcher.lake_playwright_fetcher.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__.return_value = mock_playwright_context["playwright"]

            with patch("src.scraping.fetcher.lake_playwright_fetcher.redis.from_url", return_value=mock_redis):
                with patch("src.scraping.fetcher.lake_playwright_fetcher.get_settings") as mock_settings:
                    mock_settings.return_value.playwright_headless = True
                    mock_settings.return_value.playwright_timeout_ms = 30000
                    mock_settings.return_value.playwright_session_ttl_seconds = 3600
                    mock_settings.return_value.min_html_bytes = 20
                    mock_settings.return_value.redis_url = "redis://localhost:6379"

                    result = await fetcher.fetch("https://example.com/test")

                    # Verify session loaded
                    assert result.status_code == 200
                    assert result.blocked is False

                    # Verify browser context created with storage_state
                    mock_playwright_context["browser"].new_context.assert_called_once()
                    call_args = mock_playwright_context["browser"].new_context.call_args
                    assert "storage_state" in call_args[1]

                    # Verify request count incremented
                    mock_redis.set.assert_called_once()
                    saved_data = json.loads(mock_redis.set.call_args[0][1])
                    assert saved_data["request_count"] == 6

    async def test_fetch_updates_session_after_navigation(self, fetcher, mock_playwright_context, mock_redis):
        """Test session storage_state is updated after navigation (cookies may change)."""
        mock_page = mock_playwright_context["page"]
        mock_response = MagicMock()
        mock_response.status = 200
        mock_page.goto.return_value = mock_response

        # Mock updated storage state with new cookies
        updated_storage_state = {"cookies": [{"name": "session", "value": "abc"}], "origins": []}
        mock_playwright_context["context"].storage_state.return_value = updated_storage_state

        with patch("src.scraping.fetcher.lake_playwright_fetcher.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__.return_value = mock_playwright_context["playwright"]

            with patch("src.scraping.fetcher.lake_playwright_fetcher.redis.from_url", return_value=mock_redis):
                with patch("src.scraping.fetcher.lake_playwright_fetcher.get_settings") as mock_settings:
                    mock_settings.return_value.playwright_headless = True
                    mock_settings.return_value.playwright_timeout_ms = 30000
                    mock_settings.return_value.playwright_session_ttl_seconds = 3600
                    mock_settings.return_value.min_html_bytes = 20
                    mock_settings.return_value.redis_url = "redis://localhost:6379"

                    await fetcher.fetch("https://example.com/test")

                    # Verify updated storage_state saved
                    import json

                    saved_data = json.loads(mock_redis.set.call_args[0][1])
                    assert saved_data["storage_state"] == updated_storage_state

    async def test_fetch_blocked_on_403_status(self, fetcher, mock_playwright_context, mock_redis):
        """Test fetch detects blocking on 403 Forbidden status."""
        mock_page = mock_playwright_context["page"]
        mock_response = MagicMock()
        mock_response.status = 403
        mock_page.goto.return_value = mock_response

        with patch("src.scraping.fetcher.lake_playwright_fetcher.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__.return_value = mock_playwright_context["playwright"]

            with patch("src.scraping.fetcher.lake_playwright_fetcher.redis.from_url", return_value=mock_redis):
                with patch("src.scraping.fetcher.lake_playwright_fetcher.get_settings") as mock_settings:
                    mock_settings.return_value.playwright_headless = True
                    mock_settings.return_value.playwright_timeout_ms = 30000
                    mock_settings.return_value.playwright_session_ttl_seconds = 3600
                    mock_settings.return_value.min_html_bytes = 20
                    mock_settings.return_value.redis_url = "redis://localhost:6379"

                    result = await fetcher.fetch("https://example.com/test")

                    assert result.status_code == 403
                    assert result.blocked is True

    async def test_fetch_blocked_on_tiny_html(self, fetcher, mock_playwright_context, mock_redis):
        """Test fetch detects blocking when HTML is below min_html_bytes threshold."""
        mock_page = mock_playwright_context["page"]
        mock_response = MagicMock()
        mock_response.status = 200
        mock_page.goto.return_value = mock_response
        mock_page.content.return_value = "<html></html>"  # Only 13 bytes

        with patch("src.scraping.fetcher.lake_playwright_fetcher.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__.return_value = mock_playwright_context["playwright"]

            with patch("src.scraping.fetcher.lake_playwright_fetcher.redis.from_url", return_value=mock_redis):
                with patch("src.scraping.fetcher.lake_playwright_fetcher.get_settings") as mock_settings:
                    mock_settings.return_value.playwright_headless = True
                    mock_settings.return_value.playwright_timeout_ms = 30000
                    mock_settings.return_value.playwright_session_ttl_seconds = 3600
                    mock_settings.return_value.min_html_bytes = 20
                    mock_settings.return_value.redis_url = "redis://localhost:6379"

                    result = await fetcher.fetch("https://example.com/test")

                    assert result.status_code == 200
                    assert result.blocked is True  # Tiny HTML triggers block detection

    async def test_fetch_handles_redis_connection_error(self, fetcher, mock_playwright_context):
        """Test fetch continues with fresh context when Redis is unavailable."""
        mock_page = mock_playwright_context["page"]
        mock_response = MagicMock()
        mock_response.status = 200
        mock_page.goto.return_value = mock_response

        # Mock Redis connection failure
        mock_redis_error = AsyncMock()
        mock_redis_error.get.side_effect = Exception("Redis connection failed")
        mock_redis_error.set = AsyncMock()

        with patch("src.scraping.fetcher.lake_playwright_fetcher.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__.return_value = mock_playwright_context["playwright"]

            with patch("src.scraping.fetcher.lake_playwright_fetcher.redis.from_url", return_value=mock_redis_error):
                with patch("src.scraping.fetcher.lake_playwright_fetcher.get_settings") as mock_settings:
                    mock_settings.return_value.playwright_headless = True
                    mock_settings.return_value.playwright_timeout_ms = 30000
                    mock_settings.return_value.playwright_session_ttl_seconds = 3600
                    mock_settings.return_value.min_html_bytes = 20
                    mock_settings.return_value.redis_url = "redis://localhost:6379"

                    result = await fetcher.fetch("https://example.com/test")

                    # Should still work with fresh context (no session)
                    assert result.status_code == 200
                    assert result.blocked is False

    async def test_fetch_handles_playwright_timeout(self, fetcher, mock_playwright_context, mock_redis):
        """Test fetch handles Playwright navigation timeout gracefully."""
        mock_page = mock_playwright_context["page"]
        mock_page.goto.side_effect = Exception("Timeout: 30000ms exceeded")

        with patch("src.scraping.fetcher.lake_playwright_fetcher.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__.return_value = mock_playwright_context["playwright"]

            with patch("src.scraping.fetcher.lake_playwright_fetcher.redis.from_url", return_value=mock_redis):
                with patch("src.scraping.fetcher.lake_playwright_fetcher.get_settings") as mock_settings:
                    mock_settings.return_value.playwright_headless = True
                    mock_settings.return_value.playwright_timeout_ms = 30000
                    mock_settings.return_value.playwright_session_ttl_seconds = 3600
                    mock_settings.return_value.min_html_bytes = 20
                    mock_settings.return_value.redis_url = "redis://localhost:6379"

                    result = await fetcher.fetch("https://example.com/test")

                    assert result.status_code == 0
                    assert result.blocked is True
                    assert result.html == ""

    async def test_fetch_handles_browser_launch_failure(self, fetcher, mock_playwright_context, mock_redis):
        """Test fetch handles browser launch failure gracefully."""
        mock_playwright_context["playwright"].chromium.launch.side_effect = Exception("Failed to launch browser")

        with patch("src.scraping.fetcher.lake_playwright_fetcher.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__.return_value = mock_playwright_context["playwright"]

            with patch("src.scraping.fetcher.lake_playwright_fetcher.redis.from_url", return_value=mock_redis):
                with patch("src.scraping.fetcher.lake_playwright_fetcher.get_settings") as mock_settings:
                    mock_settings.return_value.playwright_headless = True
                    mock_settings.return_value.playwright_timeout_ms = 30000
                    mock_settings.return_value.playwright_session_ttl_seconds = 3600
                    mock_settings.return_value.min_html_bytes = 20
                    mock_settings.return_value.redis_url = "redis://localhost:6379"

                    result = await fetcher.fetch("https://example.com/test")

                    assert result.status_code == 0
                    assert result.blocked is True
                    assert result.html == ""

    async def test_session_ttl_applied_correctly(self, fetcher, mock_playwright_context, mock_redis):
        """Test session is saved with correct TTL value."""
        mock_page = mock_playwright_context["page"]
        mock_response = MagicMock()
        mock_response.status = 200
        mock_page.goto.return_value = mock_response

        with patch("src.scraping.fetcher.lake_playwright_fetcher.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__.return_value = mock_playwright_context["playwright"]

            with patch("src.scraping.fetcher.lake_playwright_fetcher.redis.from_url", return_value=mock_redis):
                with patch("src.scraping.fetcher.lake_playwright_fetcher.get_settings") as mock_settings:
                    mock_settings.return_value.playwright_headless = True
                    mock_settings.return_value.playwright_timeout_ms = 30000
                    mock_settings.return_value.playwright_session_ttl_seconds = 7200  # 2 hours
                    mock_settings.return_value.min_html_bytes = 20
                    mock_settings.return_value.redis_url = "redis://localhost:6379"

                    await fetcher.fetch("https://example.com/test")

                    # Verify TTL passed to Redis
                    mock_redis.set.assert_called_once()
                    call_args = mock_redis.set.call_args
                    assert call_args[1]["ex"] == 7200  # TTL in seconds
