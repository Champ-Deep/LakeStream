from unittest.mock import patch

from src.scraping.fetcher import lake_playwright_proxy_fetcher as mod


def _reset_pool():
    """Reset module-level round-robin state between tests."""
    mod._pool_cycle = None


def _mock_settings(**overrides):
    """Create a mock settings object with proxy defaults."""
    defaults = {
        "proxy_pool_urls": "",
        "custom_proxy_url": "",
        "custom_proxy_username": "",
        "custom_proxy_password": "",
        "brightdata_proxy_url": "",
        "smartproxy_url": "",
    }
    defaults.update(overrides)

    class FakeSettings:
        pass

    s = FakeSettings()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


class TestNextPoolProxy:
    def setup_method(self):
        _reset_pool()

    def test_round_robin_rotation(self):
        settings = _mock_settings(proxy_pool_urls="http://a:3128,http://b:3128,http://c:3128")
        with patch.object(mod, "get_settings", return_value=settings):
            results = [mod._next_pool_proxy() for _ in range(6)]

        assert results == [
            "http://a:3128", "http://b:3128", "http://c:3128",
            "http://a:3128", "http://b:3128", "http://c:3128",
        ]

    def test_empty_pool_returns_none(self):
        settings = _mock_settings(proxy_pool_urls="")
        with patch.object(mod, "get_settings", return_value=settings):
            assert mod._next_pool_proxy() is None

    def test_whitespace_only_returns_none(self):
        settings = _mock_settings(proxy_pool_urls="  ,  , ")
        with patch.object(mod, "get_settings", return_value=settings):
            assert mod._next_pool_proxy() is None

    def test_single_proxy_always_returns_same(self):
        settings = _mock_settings(proxy_pool_urls="http://solo:3128")
        with patch.object(mod, "get_settings", return_value=settings):
            results = [mod._next_pool_proxy() for _ in range(3)]

        assert results == ["http://solo:3128"] * 3


class TestGetProxyChain:
    def setup_method(self):
        _reset_pool()

    def test_pool_proxy_first_in_chain(self):
        settings = _mock_settings(
            proxy_pool_urls="http://vps1:3128",
            brightdata_proxy_url="http://bright:1234",
        )
        with patch.object(mod, "get_settings", return_value=settings):
            fetcher = mod.LakePlaywrightProxyFetcher()
            chain = fetcher._get_proxy_chain()

        assert chain[0] == {"server": "http://vps1:3128"}
        assert chain[1] == {"server": "http://bright:1234"}
        assert len(chain) == 2

    def test_no_pool_preserves_existing_chain(self):
        settings = _mock_settings(
            brightdata_proxy_url="http://bright:1234",
            smartproxy_url="http://smart:5678",
        )
        with patch.object(mod, "get_settings", return_value=settings):
            fetcher = mod.LakePlaywrightProxyFetcher()
            chain = fetcher._get_proxy_chain()

        assert chain == [
            {"server": "http://bright:1234"},
            {"server": "http://smart:5678"},
        ]

    def test_full_chain_order(self):
        settings = _mock_settings(
            proxy_pool_urls="http://vps1:3128",
            custom_proxy_url="http://custom:9999",
            custom_proxy_username="user",
            custom_proxy_password="pass",
            brightdata_proxy_url="http://bright:1234",
            smartproxy_url="http://smart:5678",
        )
        with patch.object(mod, "get_settings", return_value=settings):
            fetcher = mod.LakePlaywrightProxyFetcher()
            chain = fetcher._get_proxy_chain()

        assert len(chain) == 4
        assert chain[0] == {"server": "http://vps1:3128"}
        assert chain[1] == {"server": "http://custom:9999", "username": "user", "password": "pass"}
        assert chain[2] == {"server": "http://bright:1234"}
        assert chain[3] == {"server": "http://smart:5678"}
