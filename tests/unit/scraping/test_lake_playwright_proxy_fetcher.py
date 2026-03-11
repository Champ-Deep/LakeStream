from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.scraping import ScrapingTier


class TestLakePlaywrightProxyFetcher:
    """Tests for LakePlaywrightProxyFetcher (Tier 3)."""

    @pytest.fixture
    def fetcher(self):
        from src.scraping.fetcher.lake_playwright_proxy_fetcher import LakePlaywrightProxyFetcher

        return LakePlaywrightProxyFetcher()

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

    async def test_fetch_with_custom_proxy(self, fetcher, mock_playwright_context, mock_redis):
        """Test fetching with custom proxy (priority 1)."""
        mock_page = mock_playwright_context["page"]
        mock_response = MagicMock()
        mock_response.status = 200
        mock_page.goto.return_value = mock_response

        with patch("src.scraping.fetcher.lake_playwright_proxy_fetcher.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__.return_value = mock_playwright_context["playwright"]

            with patch(
                "src.scraping.fetcher.lake_playwright_proxy_fetcher.redis.from_url", return_value=mock_redis
            ):
                with patch(
                    "src.scraping.fetcher.lake_playwright_proxy_fetcher.get_settings"
                ) as mock_settings:
                    mock_settings.return_value.playwright_headless = True
                    mock_settings.return_value.playwright_timeout_ms = 30000
                    mock_settings.return_value.playwright_session_ttl_seconds = 3600
                    mock_settings.return_value.min_html_bytes = 20
                    mock_settings.return_value.redis_url = "redis://localhost:6379"
                    mock_settings.return_value.custom_proxy_url = "http://custom-proxy:8080"
                    mock_settings.return_value.custom_proxy_username = "user"
                    mock_settings.return_value.custom_proxy_password = "pass"
                    mock_settings.return_value.brightdata_proxy_url = "http://brightdata:8080"
                    mock_settings.return_value.smartproxy_url = "http://smartproxy:8080"

                    result = await fetcher.fetch("https://example.com/test")

                    # Verify custom proxy used (priority 1)
                    assert result.tier_used == ScrapingTier.PLAYWRIGHT_PROXY
                    assert result.status_code == 200
                    assert result.cost_usd == 0.0035

                    # Verify browser context created with custom proxy
                    mock_playwright_context["browser"].new_context.assert_called_once()
                    call_args = mock_playwright_context["browser"].new_context.call_args
                    assert "proxy" in call_args[1]
                    proxy_config = call_args[1]["proxy"]
                    assert proxy_config["server"] == "http://custom-proxy:8080"
                    assert proxy_config["username"] == "user"
                    assert proxy_config["password"] == "pass"

    async def test_fetch_with_brightdata_proxy(self, fetcher, mock_playwright_context, mock_redis):
        """Test fetching with Bright Data proxy (priority 2)."""
        mock_page = mock_playwright_context["page"]
        mock_response = MagicMock()
        mock_response.status = 200
        mock_page.goto.return_value = mock_response

        with patch("src.scraping.fetcher.lake_playwright_proxy_fetcher.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__.return_value = mock_playwright_context["playwright"]

            with patch(
                "src.scraping.fetcher.lake_playwright_proxy_fetcher.redis.from_url", return_value=mock_redis
            ):
                with patch(
                    "src.scraping.fetcher.lake_playwright_proxy_fetcher.get_settings"
                ) as mock_settings:
                    mock_settings.return_value.playwright_headless = True
                    mock_settings.return_value.playwright_timeout_ms = 30000
                    mock_settings.return_value.playwright_session_ttl_seconds = 3600
                    mock_settings.return_value.min_html_bytes = 20
                    mock_settings.return_value.redis_url = "redis://localhost:6379"
                    mock_settings.return_value.custom_proxy_url = ""  # No custom proxy
                    mock_settings.return_value.custom_proxy_username = ""
                    mock_settings.return_value.custom_proxy_password = ""
                    mock_settings.return_value.brightdata_proxy_url = "http://brightdata:8080"
                    mock_settings.return_value.smartproxy_url = "http://smartproxy:8080"

                    result = await fetcher.fetch("https://example.com/test")

                    # Verify Bright Data proxy used (priority 2)
                    mock_playwright_context["browser"].new_context.assert_called_once()
                    call_args = mock_playwright_context["browser"].new_context.call_args
                    assert "proxy" in call_args[1]
                    proxy_config = call_args[1]["proxy"]
                    assert proxy_config["server"] == "http://brightdata:8080"

    async def test_fetch_with_smartproxy_fallback(self, fetcher, mock_playwright_context, mock_redis):
        """Test fetching with Smartproxy fallback (priority 3)."""
        mock_page = mock_playwright_context["page"]
        mock_response = MagicMock()
        mock_response.status = 200
        mock_page.goto.return_value = mock_response

        with patch("src.scraping.fetcher.lake_playwright_proxy_fetcher.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__.return_value = mock_playwright_context["playwright"]

            with patch(
                "src.scraping.fetcher.lake_playwright_proxy_fetcher.redis.from_url", return_value=mock_redis
            ):
                with patch(
                    "src.scraping.fetcher.lake_playwright_proxy_fetcher.get_settings"
                ) as mock_settings:
                    mock_settings.return_value.playwright_headless = True
                    mock_settings.return_value.playwright_timeout_ms = 30000
                    mock_settings.return_value.playwright_session_ttl_seconds = 3600
                    mock_settings.return_value.min_html_bytes = 20
                    mock_settings.return_value.redis_url = "redis://localhost:6379"
                    mock_settings.return_value.custom_proxy_url = ""  # No custom proxy
                    mock_settings.return_value.custom_proxy_username = ""
                    mock_settings.return_value.custom_proxy_password = ""
                    mock_settings.return_value.brightdata_proxy_url = ""  # No Bright Data
                    mock_settings.return_value.smartproxy_url = "http://smartproxy:8080"

                    result = await fetcher.fetch("https://example.com/test")

                    # Verify Smartproxy used (priority 3)
                    mock_playwright_context["browser"].new_context.assert_called_once()
                    call_args = mock_playwright_context["browser"].new_context.call_args
                    assert "proxy" in call_args[1]
                    proxy_config = call_args[1]["proxy"]
                    assert proxy_config["server"] == "http://smartproxy:8080"

    async def test_fetch_no_proxy_available(self, fetcher, mock_playwright_context, mock_redis):
        """Test fetching without proxy (falls back to PLAYWRIGHT behavior)."""
        mock_page = mock_playwright_context["page"]
        mock_response = MagicMock()
        mock_response.status = 200
        mock_page.goto.return_value = mock_response

        with patch("src.scraping.fetcher.lake_playwright_proxy_fetcher.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__.return_value = mock_playwright_context["playwright"]

            with patch(
                "src.scraping.fetcher.lake_playwright_proxy_fetcher.redis.from_url", return_value=mock_redis
            ):
                with patch(
                    "src.scraping.fetcher.lake_playwright_proxy_fetcher.get_settings"
                ) as mock_settings:
                    mock_settings.return_value.playwright_headless = True
                    mock_settings.return_value.playwright_timeout_ms = 30000
                    mock_settings.return_value.playwright_session_ttl_seconds = 3600
                    mock_settings.return_value.min_html_bytes = 20
                    mock_settings.return_value.redis_url = "redis://localhost:6379"
                    mock_settings.return_value.custom_proxy_url = ""
                    mock_settings.return_value.custom_proxy_username = ""
                    mock_settings.return_value.custom_proxy_password = ""
                    mock_settings.return_value.brightdata_proxy_url = ""
                    mock_settings.return_value.smartproxy_url = ""

                    result = await fetcher.fetch("https://example.com/test")

                    # Verify no proxy used
                    mock_playwright_context["browser"].new_context.assert_called_once()
                    call_args = mock_playwright_context["browser"].new_context.call_args
                    assert "proxy" not in call_args[1] or call_args[1].get("proxy") is None

    async def test_fetch_session_and_proxy_combined(self, fetcher, mock_playwright_context, mock_redis):
        """Test fetching with both session persistence and proxy."""
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

        with patch("src.scraping.fetcher.lake_playwright_proxy_fetcher.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__.return_value = mock_playwright_context["playwright"]

            with patch(
                "src.scraping.fetcher.lake_playwright_proxy_fetcher.redis.from_url", return_value=mock_redis
            ):
                with patch(
                    "src.scraping.fetcher.lake_playwright_proxy_fetcher.get_settings"
                ) as mock_settings:
                    mock_settings.return_value.playwright_headless = True
                    mock_settings.return_value.playwright_timeout_ms = 30000
                    mock_settings.return_value.playwright_session_ttl_seconds = 3600
                    mock_settings.return_value.min_html_bytes = 20
                    mock_settings.return_value.redis_url = "redis://localhost:6379"
                    mock_settings.return_value.custom_proxy_url = "http://custom-proxy:8080"
                    mock_settings.return_value.custom_proxy_username = ""
                    mock_settings.return_value.custom_proxy_password = ""
                    mock_settings.return_value.brightdata_proxy_url = ""
                    mock_settings.return_value.smartproxy_url = ""

                    result = await fetcher.fetch("https://example.com/test")

                    # Verify both session and proxy used
                    mock_playwright_context["browser"].new_context.assert_called_once()
                    call_args = mock_playwright_context["browser"].new_context.call_args
                    assert "storage_state" in call_args[1]
                    assert "proxy" in call_args[1]

                    # Verify request count incremented
                    mock_redis.set.assert_called_once()
                    saved_data = json.loads(mock_redis.set.call_args[0][1])
                    assert saved_data["request_count"] == 6
                    assert saved_data["proxy_used"] == "http://custom-proxy:8080"

    async def test_fetch_blocked_on_403_status(self, fetcher, mock_playwright_context, mock_redis):
        """Test fetch detects blocking on 403 Forbidden status."""
        mock_page = mock_playwright_context["page"]
        mock_response = MagicMock()
        mock_response.status = 403
        mock_page.goto.return_value = mock_response

        with patch("src.scraping.fetcher.lake_playwright_proxy_fetcher.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__.return_value = mock_playwright_context["playwright"]

            with patch(
                "src.scraping.fetcher.lake_playwright_proxy_fetcher.redis.from_url", return_value=mock_redis
            ):
                with patch(
                    "src.scraping.fetcher.lake_playwright_proxy_fetcher.get_settings"
                ) as mock_settings:
                    mock_settings.return_value.playwright_headless = True
                    mock_settings.return_value.playwright_timeout_ms = 30000
                    mock_settings.return_value.playwright_session_ttl_seconds = 3600
                    mock_settings.return_value.min_html_bytes = 20
                    mock_settings.return_value.redis_url = "redis://localhost:6379"
                    mock_settings.return_value.custom_proxy_url = ""
                    mock_settings.return_value.custom_proxy_username = ""
                    mock_settings.return_value.custom_proxy_password = ""
                    mock_settings.return_value.brightdata_proxy_url = ""
                    mock_settings.return_value.smartproxy_url = ""

                    result = await fetcher.fetch("https://example.com/test")

                    assert result.status_code == 403
                    assert result.blocked is True

    async def test_fetch_proxy_connection_failure(self, fetcher, mock_playwright_context, mock_redis):
        """Test fetch handles proxy connection failure gracefully."""
        mock_page = mock_playwright_context["page"]
        mock_page.goto.side_effect = Exception("Proxy connection failed")

        with patch("src.scraping.fetcher.lake_playwright_proxy_fetcher.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__.return_value = mock_playwright_context["playwright"]

            with patch(
                "src.scraping.fetcher.lake_playwright_proxy_fetcher.redis.from_url", return_value=mock_redis
            ):
                with patch(
                    "src.scraping.fetcher.lake_playwright_proxy_fetcher.get_settings"
                ) as mock_settings:
                    mock_settings.return_value.playwright_headless = True
                    mock_settings.return_value.playwright_timeout_ms = 30000
                    mock_settings.return_value.playwright_session_ttl_seconds = 3600
                    mock_settings.return_value.min_html_bytes = 20
                    mock_settings.return_value.redis_url = "redis://localhost:6379"
                    mock_settings.return_value.custom_proxy_url = "http://custom-proxy:8080"
                    mock_settings.return_value.custom_proxy_username = ""
                    mock_settings.return_value.custom_proxy_password = ""
                    mock_settings.return_value.brightdata_proxy_url = ""
                    mock_settings.return_value.smartproxy_url = ""

                    result = await fetcher.fetch("https://example.com/test")

                    assert result.status_code == 0
                    assert result.blocked is True
                    assert result.html == ""
