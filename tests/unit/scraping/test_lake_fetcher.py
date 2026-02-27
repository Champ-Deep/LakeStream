import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.scraping import ScrapingTier


class MockResponse:
    """Mock response object."""

    def __init__(self, html_content: str = "", status: int = 200):
        self.html_content = html_content
        self.status = status


class TestLakeFetcher:
    """Tests for LakeFetcher (Tier 1)."""

    @pytest.fixture
    def fetcher(self):
        from src.scraping.fetcher.lake_fetcher import LakeFetcher

        return LakeFetcher()

    @pytest.mark.asyncio
    async def test_fetch_blocked_403(self, fetcher):
        mock_response = MockResponse(html_content="", status=403)

        with patch.object(fetcher.fetcher, "get", return_value=mock_response):
            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.return_value = mock_response

                result = await fetcher.fetch("https://example.com")

                assert result.status_code == 403
                assert result.blocked is True

    @pytest.mark.asyncio
    async def test_fetch_blocked_small_content(self, fetcher):
        mock_response = MockResponse(html_content="<html></html>", status=200)

        with patch.object(fetcher.fetcher, "get", return_value=mock_response):
            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.return_value = mock_response

                result = await fetcher.fetch("https://example.com")

                assert result.blocked is True

    @pytest.mark.asyncio
    async def test_fetch_captcha_detected(self, fetcher):
        html_with_captcha = '<html><body><div class="g-recaptcha">Captcha</div></body></html>'
        mock_response = MockResponse(html_content=html_with_captcha, status=200)

        with patch.object(fetcher.fetcher, "get", return_value=mock_response):
            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.return_value = mock_response

                result = await fetcher.fetch("https://example.com")

                assert result.captcha_detected is True

    @pytest.mark.asyncio
    async def test_fetch_exception(self, fetcher):
        with patch.object(fetcher.fetcher, "get", side_effect=Exception("Network error")):
            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = Exception("Network error")

                result = await fetcher.fetch("https://example.com")

                assert result.status_code == 0
                assert result.blocked is True
                assert result.html == ""


class TestLakeStealthFetcher:
    """Tests for LakeStealthFetcher (Tier 2)."""

    @pytest.fixture
    def fetcher(self):
        from src.scraping.fetcher.lake_stealth_fetcher import LakeStealthFetcher

        return LakeStealthFetcher()

    @pytest.mark.asyncio
    async def test_fetch_blocked_429(self, fetcher):
        mock_response = MockResponse(html_content="", status=429)

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.return_value = mock_response

            with patch("src.scraping.fetcher.lake_stealth_fetcher.StealthyFetcher") as MockFetcher:
                mock_fetcher_instance = MagicMock()
                mock_fetcher_instance.fetch.return_value = mock_response
                MockFetcher.return_value = mock_fetcher_instance

                result = await fetcher.fetch("https://example.com")

                assert result.status_code == 429
                assert result.blocked is True

    @pytest.mark.asyncio
    async def test_stealth_fetch_exception(self, fetcher):
        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.side_effect = Exception("Browser error")

            with patch("src.scraping.fetcher.lake_stealth_fetcher.StealthyFetcher") as MockFetcher:
                mock_fetcher_instance = MagicMock()
                mock_fetcher_instance.fetch.side_effect = Exception("Browser error")
                MockFetcher.return_value = mock_fetcher_instance

                result = await fetcher.fetch("https://example.com")

                assert result.status_code == 0
                assert result.blocked is True


class TestLakeProxyFetcher:
    """Tests for LakeProxyFetcher (Tier 3)."""

    @pytest.fixture
    def fetcher(self):
        from src.scraping.fetcher.lake_proxy_fetcher import LakeProxyFetcher

        return LakeProxyFetcher()

    @pytest.mark.asyncio
    async def test_fetch_with_proxy(self, fetcher):
        mock_response = MockResponse(html_content="<html><body>Proxied</body></html>", status=200)

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.return_value = mock_response

            with patch("src.scraping.fetcher.lake_proxy_fetcher.StealthyFetcher") as MockFetcher:
                with patch("src.scraping.fetcher.lake_proxy_fetcher.get_settings") as mock_settings:
                    mock_settings.return_value.brightdata_proxy_url = "http://proxy:8080"
                    mock_settings.return_value.smartproxy_url = ""

                    mock_fetcher_instance = MagicMock()
                    mock_fetcher_instance.fetch.return_value = mock_response
                    MockFetcher.return_value = mock_fetcher_instance

                    result = await fetcher.fetch("https://example.com")

                    assert result.tier_used == ScrapingTier.HEADLESS_PROXY
                    assert result.cost_usd == 0.004

    @pytest.mark.asyncio
    async def test_fetch_without_proxy(self, fetcher):
        mock_response = MockResponse(html_content="<html><body>No proxy</body></html>", status=200)

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.return_value = mock_response

            with patch("src.scraping.fetcher.lake_proxy_fetcher.StealthyFetcher") as MockFetcher:
                with patch("src.scraping.fetcher.lake_proxy_fetcher.get_settings") as mock_settings:
                    mock_settings.return_value.brightdata_proxy_url = ""
                    mock_settings.return_value.smartproxy_url = ""

                    mock_fetcher_instance = MagicMock()
                    mock_fetcher_instance.fetch.return_value = mock_response
                    MockFetcher.return_value = mock_fetcher_instance

                    result = await fetcher.fetch("https://example.com")

                    assert result.tier_used == ScrapingTier.HEADLESS_PROXY
