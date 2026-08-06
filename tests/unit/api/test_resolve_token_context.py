"""Unit tests for resolve_token_context() — the single auth verification seam.

Covers the Clerk path (verified claims → lazy-provisioned local user), the
legacy HS256 fallback, and the enable_legacy_jwt=false hard cutoff.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.api.middleware import auth as auth_middleware
from src.api.middleware.auth import resolve_token_context
from src.services.auth import create_access_token
from src.services.clerk import ClerkAuthError


def _settings(**overrides) -> SimpleNamespace:
    base = {
        "auth_provider": "legacy",
        "enable_legacy_jwt": True,
        "clerk_link_by_email": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _local_user():
    return SimpleNamespace(
        id=uuid4(), org_id=uuid4(), role="member", is_admin=False
    )


class TestLegacyPath:
    @pytest.mark.asyncio
    async def test_valid_legacy_token_resolves(self):
        user_id, org_id = uuid4(), uuid4()
        token = create_access_token(user_id, org_id, "member")
        with patch.object(auth_middleware, "get_settings", return_value=_settings()):
            ctx = await resolve_token_context(token)
        assert ctx == {
            "user_id": str(user_id),
            "org_id": str(org_id),
            "role": "member",
            "is_admin": False,
        }

    @pytest.mark.asyncio
    async def test_garbage_token_returns_none(self):
        with patch.object(auth_middleware, "get_settings", return_value=_settings()):
            assert await resolve_token_context("not-a-jwt") is None


class TestClerkPath:
    @pytest.mark.asyncio
    async def test_valid_clerk_token_provisions_and_resolves(self):
        user = _local_user()
        with (
            patch.object(
                auth_middleware, "get_settings",
                return_value=_settings(auth_provider="clerk"),
            ),
            patch(
                "src.services.clerk.verify_clerk_token",
                return_value={"sub": "user_1", "email": "a@b.co"},
            ),
            patch(
                "src.services.clerk.claims_to_context",
                return_value={
                    "clerk_user_id": "user_1", "email": "a@b.co",
                    "role": "member", "is_admin": False,
                },
            ),
            patch(
                "src.db.queries.users.get_or_create_by_clerk_id",
                new_callable=AsyncMock,
                return_value=user,
            ),
            patch.object(
                auth_middleware, "get_pool", new_callable=AsyncMock, return_value=MagicMock()
            ),
        ):
            ctx = await resolve_token_context("clerk-token")
        assert ctx == {
            "user_id": str(user.id),
            "org_id": str(user.org_id),
            "role": "member",
            "is_admin": False,
        }

    @pytest.mark.asyncio
    async def test_bad_clerk_token_without_legacy_fallback_returns_none(self):
        with (
            patch.object(
                auth_middleware, "get_settings",
                return_value=_settings(auth_provider="clerk", enable_legacy_jwt=False),
            ),
            patch("src.services.clerk.verify_clerk_token", side_effect=ClerkAuthError("bad")),
        ):
            assert await resolve_token_context("bad-token") is None

    @pytest.mark.asyncio
    async def test_bad_clerk_token_falls_back_to_legacy_when_enabled(self):
        user_id, org_id = uuid4(), uuid4()
        legacy_token = create_access_token(user_id, org_id, "member")
        with (
            patch.object(
                auth_middleware, "get_settings",
                return_value=_settings(auth_provider="clerk", enable_legacy_jwt=True),
            ),
            patch(
                "src.services.clerk.verify_clerk_token",
                side_effect=ClerkAuthError("not a clerk token"),
            ),
        ):
            ctx = await resolve_token_context(legacy_token)
        assert ctx is not None
        assert ctx["user_id"] == str(user_id)

    @pytest.mark.asyncio
    async def test_legacy_token_rejected_after_cutoff(self):
        """With clerk active and legacy disabled, an old HS256 token is dead."""
        legacy_token = create_access_token(uuid4(), uuid4(), "member")
        with (
            patch.object(
                auth_middleware, "get_settings",
                return_value=_settings(auth_provider="clerk", enable_legacy_jwt=False),
            ),
            patch(
                "src.services.clerk.verify_clerk_token",
                side_effect=ClerkAuthError("not a clerk token"),
            ),
        ):
            assert await resolve_token_context(legacy_token) is None
