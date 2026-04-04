"""Tests for proxy health tracking and weighted selection."""

import json
import time
from unittest.mock import AsyncMock

from src.services.proxy_health import (
    BACKOFF_SECONDS,
    MAX_CONSECUTIVE_FAILURES,
    REGION_HEADERS,
    ProxyHealth,
    ProxyHealthTracker,
    get_region_headers,
    get_region_timezone,
)


class TestProxyHealth:
    """Tests for the ProxyHealth dataclass."""

    def test_default_success_rate_is_one(self):
        h = ProxyHealth(url="http://proxy1:3128")
        assert h.success_rate == 1.0

    def test_success_rate_with_data(self):
        h = ProxyHealth(url="http://proxy1:3128", success_count=8, fail_count=2)
        assert h.success_rate == 0.8

    def test_is_backed_off_false_when_few_failures(self):
        h = ProxyHealth(
            url="http://proxy1:3128",
            consecutive_failures=3,
            last_failure_at=time.time(),
        )
        assert h.is_backed_off is False

    def test_is_backed_off_true_when_many_recent_failures(self):
        h = ProxyHealth(
            url="http://proxy1:3128",
            consecutive_failures=MAX_CONSECUTIVE_FAILURES,
            last_failure_at=time.time(),
        )
        assert h.is_backed_off is True

    def test_is_backed_off_false_after_backoff_period(self):
        h = ProxyHealth(
            url="http://proxy1:3128",
            consecutive_failures=MAX_CONSECUTIVE_FAILURES,
            last_failure_at=time.time() - BACKOFF_SECONDS - 1,
        )
        assert h.is_backed_off is False

    def test_total_requests(self):
        h = ProxyHealth(url="http://proxy1:3128", success_count=10, fail_count=5)
        assert h.total_requests == 15


class TestProxyHealthTracker:
    """Tests for the ProxyHealthTracker Redis-backed tracker."""

    async def test_record_success_updates_stats(self):
        tracker = ProxyHealthTracker()
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None  # No existing data
        tracker._redis = mock_redis

        await tracker.record_success("http://proxy1:3128", 150)

        # Should have saved to Redis
        mock_redis.set.assert_called_once()
        saved = json.loads(mock_redis.set.call_args[0][1])
        assert saved["success_count"] == 1
        assert saved["avg_latency_ms"] == 150
        assert saved["consecutive_failures"] == 0

    async def test_record_failure_increments_failures(self):
        tracker = ProxyHealthTracker()
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None
        tracker._redis = mock_redis

        await tracker.record_failure("http://proxy1:3128")

        saved = json.loads(mock_redis.set.call_args[0][1])
        assert saved["fail_count"] == 1
        assert saved["consecutive_failures"] == 1
        assert saved["last_failure_at"] > 0

    async def test_record_success_resets_consecutive_failures(self):
        tracker = ProxyHealthTracker()
        mock_redis = AsyncMock()

        # Existing data with failures
        existing = json.dumps({
            "url": "http://proxy1:3128",
            "region": None,
            "success_count": 5,
            "fail_count": 3,
            "avg_latency_ms": 200,
            "last_failure_at": time.time(),
            "consecutive_failures": 3,
        })
        mock_redis.get.return_value = existing
        tracker._redis = mock_redis

        await tracker.record_success("http://proxy1:3128", 100)

        saved = json.loads(mock_redis.set.call_args[0][1])
        assert saved["consecutive_failures"] == 0
        assert saved["success_count"] == 6

    async def test_pick_proxy_returns_none_for_empty_list(self):
        tracker = ProxyHealthTracker()
        mock_redis = AsyncMock()
        tracker._redis = mock_redis

        result = await tracker.pick_proxy([])
        assert result is None

    async def test_pick_proxy_selects_from_candidates(self):
        tracker = ProxyHealthTracker()
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None  # Fresh proxies
        tracker._redis = mock_redis

        proxies = [
            {"url": "http://proxy1:3128", "region": "us"},
            {"url": "http://proxy2:3128", "region": "eu"},
        ]

        result = await tracker.pick_proxy(proxies)
        assert result is not None
        assert result["url"] in ("http://proxy1:3128", "http://proxy2:3128")

    async def test_pick_proxy_filters_by_region(self):
        tracker = ProxyHealthTracker()
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None  # Fresh
        tracker._redis = mock_redis

        proxies = [
            {"url": "http://us-proxy:3128", "region": "us"},
            {"url": "http://eu-proxy:3128", "region": "eu"},
        ]

        result = await tracker.pick_proxy(proxies, region="eu")
        assert result is not None
        assert result["url"] == "http://eu-proxy:3128"

    async def test_pick_proxy_falls_back_when_no_region_match(self):
        tracker = ProxyHealthTracker()
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None
        tracker._redis = mock_redis

        proxies = [
            {"url": "http://us-proxy:3128", "region": "us"},
        ]

        # Region "asia" not in pool — should fall back to all proxies
        result = await tracker.pick_proxy(proxies, region="asia")
        assert result is not None
        assert result["url"] == "http://us-proxy:3128"

    async def test_pick_proxy_skips_backed_off_proxies(self):
        tracker = ProxyHealthTracker()
        mock_redis = AsyncMock()

        # Proxy 1: backed off (many recent failures)
        backed_off = json.dumps({
            "url": "http://bad-proxy:3128",
            "region": None,
            "success_count": 0,
            "fail_count": 10,
            "avg_latency_ms": 5000,
            "last_failure_at": time.time(),
            "consecutive_failures": MAX_CONSECUTIVE_FAILURES,
        })
        # Proxy 2: healthy
        healthy = json.dumps({
            "url": "http://good-proxy:3128",
            "region": None,
            "success_count": 50,
            "fail_count": 2,
            "avg_latency_ms": 100,
            "last_failure_at": 0,
            "consecutive_failures": 0,
        })

        def side_effect(key):
            if "bad" in str(key) or key == tracker._key("http://bad-proxy:3128"):
                return backed_off
            return healthy

        mock_redis.get.side_effect = side_effect
        tracker._redis = mock_redis

        proxies = [
            {"url": "http://bad-proxy:3128"},
            {"url": "http://good-proxy:3128"},
        ]

        # Should always pick good proxy since bad is backed off
        for _ in range(10):
            result = await tracker.pick_proxy(proxies)
            assert result["url"] == "http://good-proxy:3128"


class TestRegionHelpers:
    """Tests for region header and timezone helpers."""

    def test_get_region_headers_returns_accept_language(self):
        headers = get_region_headers("us")
        assert "Accept-Language" in headers
        assert "en-US" in headers["Accept-Language"]

    def test_get_region_headers_returns_empty_for_none(self):
        assert get_region_headers(None) == {}

    def test_get_region_headers_case_insensitive(self):
        assert get_region_headers("US") == get_region_headers("us")

    def test_get_region_timezone_default(self):
        assert get_region_timezone(None) == "America/New_York"

    def test_get_region_timezone_eu(self):
        assert get_region_timezone("eu") == "Europe/London"

    def test_get_region_timezone_unknown_region(self):
        assert get_region_timezone("mars") == "America/New_York"

    def test_all_regions_have_headers(self):
        for region in REGION_HEADERS:
            headers = get_region_headers(region)
            assert "Accept-Language" in headers
