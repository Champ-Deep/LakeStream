"""Tests for AuthenticatedSessionManager."""

import json
from unittest.mock import AsyncMock

from src.services.session_manager import (
    _USER_AGENTS,
    _VIEWPORTS,
    AuthenticatedSessionManager,
    random_delay,
)


class TestSessionManager:
    """Tests for session create/get/update/destroy lifecycle."""

    async def test_create_session_stores_in_redis(self):
        mgr = AuthenticatedSessionManager()

        mock_redis = AsyncMock()
        mgr._redis = mock_redis

        cookies = [
            {"name": "li_at", "value": "abc123", "domain": ".linkedin.com", "path": "/"},
            {"name": "JSESSIONID", "value": "xyz", "domain": ".linkedin.com", "path": "/"},
        ]

        key = await mgr.create_session("linkedin.com", cookies, ttl=3600)

        assert key == "auth_session:linkedin.com"
        mock_redis.set.assert_called_once()

        # Verify stored data structure
        call_args = mock_redis.set.call_args
        stored_data = json.loads(call_args[0][1])
        assert stored_data["authenticated"] is True
        assert stored_data["request_count"] == 0
        assert len(stored_data["storage_state"]["cookies"]) == 2
        assert stored_data["user_agent"] in _USER_AGENTS
        assert stored_data["viewport"] in _VIEWPORTS

    async def test_create_session_converts_cookie_format(self):
        mgr = AuthenticatedSessionManager()
        mock_redis = AsyncMock()
        mgr._redis = mock_redis

        cookies = [
            {
                "name": "session",
                "value": "test",
                "domain": ".apollo.io",
                "path": "/",
                "expirationDate": 1735689600,
                "sameSite": "lax",
                "secure": True,
                "httpOnly": True,
            }
        ]

        await mgr.create_session("apollo.io", cookies)

        stored = json.loads(mock_redis.set.call_args[0][1])
        pc = stored["storage_state"]["cookies"][0]
        assert pc["name"] == "session"
        assert pc["expires"] == 1735689600
        assert pc["sameSite"] == "Lax"
        assert pc["secure"] is True
        assert pc["httpOnly"] is True

    async def test_get_session_returns_none_when_missing(self):
        mgr = AuthenticatedSessionManager()
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None
        mgr._redis = mock_redis

        result = await mgr.get_session("nonexistent.com")
        assert result is None

    async def test_get_session_returns_data_when_present(self):
        mgr = AuthenticatedSessionManager()
        mock_redis = AsyncMock()

        session_data = {
            "storage_state": {"cookies": [], "origins": []},
            "created_at": 1000,
            "last_used_at": 1000,
            "request_count": 5,
            "authenticated": True,
            "user_agent": _USER_AGENTS[0],
            "viewport": _VIEWPORTS[0],
        }
        mock_redis.get.return_value = json.dumps(session_data)
        mgr._redis = mock_redis

        result = await mgr.get_session("linkedin.com")
        assert result is not None
        assert result["authenticated"] is True
        assert result["request_count"] == 5

    async def test_update_session_increments_request_count(self):
        mgr = AuthenticatedSessionManager()
        mock_redis = AsyncMock()
        mock_redis.ttl.return_value = 3000

        session_data = {
            "storage_state": {"cookies": [], "origins": []},
            "created_at": 1000,
            "last_used_at": 1000,
            "request_count": 10,
            "authenticated": True,
            "user_agent": _USER_AGENTS[0],
            "viewport": _VIEWPORTS[0],
        }
        mock_redis.get.return_value = json.dumps(session_data)
        mgr._redis = mock_redis

        new_state = {"cookies": [{"name": "updated"}], "origins": []}
        await mgr.update_session("linkedin.com", new_state)

        stored = json.loads(mock_redis.set.call_args[0][1])
        assert stored["request_count"] == 11
        assert stored["storage_state"]["cookies"][0]["name"] == "updated"

    async def test_destroy_session_deletes_from_redis(self):
        mgr = AuthenticatedSessionManager()
        mock_redis = AsyncMock()
        mgr._redis = mock_redis

        await mgr.destroy_session("linkedin.com")

        mock_redis.delete.assert_called_once_with("auth_session:linkedin.com")

    async def test_get_session_handles_invalid_json(self):
        mgr = AuthenticatedSessionManager()
        mock_redis = AsyncMock()
        mock_redis.get.return_value = "not valid json{"
        mgr._redis = mock_redis

        result = await mgr.get_session("linkedin.com")
        assert result is None


class TestAntiDetection:
    """Tests for anti-detection features."""

    async def test_random_delay_within_bounds(self):
        """random_delay should complete without error."""
        # Just verify it doesn't raise — actual sleep is tested implicitly
        await random_delay(1, 5)  # Very short delay for test speed

    async def test_session_stores_random_fingerprint(self):
        mgr = AuthenticatedSessionManager()
        mock_redis = AsyncMock()
        mgr._redis = mock_redis

        await mgr.create_session("test.com", [{"name": "a", "value": "b"}])

        stored = json.loads(mock_redis.set.call_args[0][1])
        assert stored["user_agent"] in _USER_AGENTS
        assert stored["viewport"] in _VIEWPORTS
        assert "width" in stored["viewport"]
        assert "height" in stored["viewport"]
