"""Tests for proxy pool parsing and proxy chain construction.

Updated to match the health-aware proxy selection architecture.
The old round-robin _next_pool_proxy() has been replaced by
ProxyHealthTracker.pick_proxy() — those tests are in test_proxy_health.py.
"""

import json
from unittest.mock import AsyncMock, patch

from src.scraping.fetcher import lake_playwright_proxy_fetcher as mod


def _mock_settings(**overrides):
    """Create a mock settings object with proxy defaults."""
    defaults = {
        "proxy_pool_urls": "",
        "proxy_pool_config": "",
        "custom_proxy_url": "",
        "custom_proxy_username": "",
        "custom_proxy_password": "",
        "brightdata_proxy_url": "",
        "smartproxy_url": "",
        "redis_url": "redis://localhost:6379",
    }
    defaults.update(overrides)

    class FakeSettings:
        pass

    s = FakeSettings()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


class TestGetPoolProxies:
    """Tests for the _get_pool_proxies helper."""

    def test_empty_returns_empty_list(self):
        settings = _mock_settings()
        with patch.object(mod, "get_settings", return_value=settings):
            result = mod._get_pool_proxies()
        assert result == []

    def test_comma_separated_urls(self):
        settings = _mock_settings(
            proxy_pool_urls="http://a:3128,http://b:3128,http://c:3128",
        )
        with patch.object(mod, "get_settings", return_value=settings):
            result = mod._get_pool_proxies()

        assert len(result) == 3
        assert result[0] == {"url": "http://a:3128"}
        assert result[1] == {"url": "http://b:3128"}

    def test_json_config_with_regions(self):
        config = json.dumps([
            {"url": "http://us-vps:3128", "region": "us"},
            {"url": "http://eu-vps:3128", "region": "eu"},
        ])
        settings = _mock_settings(proxy_pool_config=config)
        with patch.object(mod, "get_settings", return_value=settings):
            result = mod._get_pool_proxies()

        assert len(result) == 2
        assert result[0]["region"] == "us"
        assert result[1]["region"] == "eu"

    def test_json_config_takes_precedence_over_urls(self):
        config = json.dumps([{"url": "http://json-proxy:3128"}])
        settings = _mock_settings(
            proxy_pool_config=config,
            proxy_pool_urls="http://url-proxy:3128",
        )
        with patch.object(mod, "get_settings", return_value=settings):
            result = mod._get_pool_proxies()

        assert len(result) == 1
        assert result[0]["url"] == "http://json-proxy:3128"

    def test_whitespace_only_returns_empty(self):
        settings = _mock_settings(proxy_pool_urls="  ,  , ")
        with patch.object(mod, "get_settings", return_value=settings):
            result = mod._get_pool_proxies()
        assert result == []

    def test_invalid_json_falls_back_to_urls(self):
        settings = _mock_settings(
            proxy_pool_config="not json{",
            proxy_pool_urls="http://fallback:3128",
        )
        with patch.object(mod, "get_settings", return_value=settings):
            result = mod._get_pool_proxies()

        assert len(result) == 1
        assert result[0]["url"] == "http://fallback:3128"


class TestGetProxyChain:
    """Tests for the async _get_proxy_chain method."""

    async def test_no_pool_preserves_existing_chain(self):
        settings = _mock_settings(
            brightdata_proxy_url="http://bright:1234",
            smartproxy_url="http://smart:5678",
        )
        with patch.object(mod, "get_settings", return_value=settings):
            fetcher = mod.LakePlaywrightProxyFetcher()
            chain = await fetcher._get_proxy_chain()

        assert chain == [
            {"server": "http://bright:1234"},
            {"server": "http://smart:5678"},
        ]

    async def test_pool_proxy_first_in_chain(self):
        settings = _mock_settings(
            proxy_pool_urls="http://vps1:3128",
            brightdata_proxy_url="http://bright:1234",
        )

        # Mock the health tracker to return the pool proxy
        mock_tracker = AsyncMock()
        mock_tracker.pick_proxy.return_value = {"url": "http://vps1:3128"}

        with (
            patch.object(mod, "get_settings", return_value=settings),
            patch.object(mod, "_get_health_tracker", return_value=mock_tracker),
        ):
            fetcher = mod.LakePlaywrightProxyFetcher()
            chain = await fetcher._get_proxy_chain()

        assert chain[0] == {"server": "http://vps1:3128"}
        assert chain[1] == {"server": "http://bright:1234"}
        assert len(chain) == 2

    async def test_full_chain_order(self):
        settings = _mock_settings(
            proxy_pool_urls="http://vps1:3128",
            custom_proxy_url="http://custom:9999",
            custom_proxy_username="user",
            custom_proxy_password="pass",
            brightdata_proxy_url="http://bright:1234",
            smartproxy_url="http://smart:5678",
        )

        mock_tracker = AsyncMock()
        mock_tracker.pick_proxy.return_value = {"url": "http://vps1:3128"}

        with (
            patch.object(mod, "get_settings", return_value=settings),
            patch.object(mod, "_get_health_tracker", return_value=mock_tracker),
        ):
            fetcher = mod.LakePlaywrightProxyFetcher()
            chain = await fetcher._get_proxy_chain()

        assert len(chain) == 4
        assert chain[0] == {"server": "http://vps1:3128"}
        assert chain[1] == {
            "server": "http://custom:9999",
            "username": "user",
            "password": "pass",
        }
        assert chain[2] == {"server": "http://bright:1234"}
        assert chain[3] == {"server": "http://smart:5678"}

    async def test_region_passed_to_health_tracker(self):
        settings = _mock_settings(
            proxy_pool_urls="http://vps1:3128",
        )

        mock_tracker = AsyncMock()
        mock_tracker.pick_proxy.return_value = {"url": "http://vps1:3128"}

        with (
            patch.object(mod, "get_settings", return_value=settings),
            patch.object(mod, "_get_health_tracker", return_value=mock_tracker),
        ):
            fetcher = mod.LakePlaywrightProxyFetcher()
            await fetcher._get_proxy_chain(region="eu")

        mock_tracker.pick_proxy.assert_called_once()
        call_kwargs = mock_tracker.pick_proxy.call_args
        assert call_kwargs.kwargs.get("region") == "eu"
