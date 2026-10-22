"""Tests for the Clerk webhook receiver (/api/webhooks/clerk).

Signatures are computed with the real svix library (the same one the route
verifies with), so the signature path is exercised end-to-end — no mocked
verification.
"""

import base64
import json
import secrets
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from svix.webhooks import Webhook

from src.api.routes.clerk_webhooks import router

SECRET = "whsec_" + base64.b64encode(secrets.token_bytes(24)).decode()


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return app


def _signed_headers(payload: str) -> dict:
    msg_id = f"msg_{secrets.token_hex(8)}"
    timestamp = datetime.now(UTC)
    signature = Webhook(SECRET).sign(msg_id, timestamp, payload)
    return {
        "svix-id": msg_id,
        "svix-timestamp": str(int(timestamp.timestamp())),
        "svix-signature": signature,
    }


def _settings(secret: str = SECRET) -> SimpleNamespace:
    return SimpleNamespace(clerk_webhook_signing_secret=secret)


def _post(client: TestClient, event: dict, *, headers: dict | None = None):
    payload = json.dumps(event)
    return client.post(
        "/api/webhooks/clerk",
        content=payload,
        headers={"Content-Type": "application/json", **(headers or _signed_headers(payload))},
    )


class TestSignatureVerification:
    def test_valid_signature_accepted(self):
        with (
            patch("src.api.routes.clerk_webhooks.get_settings", return_value=_settings()),
            patch(
                "src.api.routes.clerk_webhooks.get_pool",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
        ):
            r = _post(TestClient(_app()), {"type": "unknown.event", "data": {}})
        assert r.status_code == 200
        assert r.json() == {"received": True}

    def test_bad_signature_rejected(self):
        payload = json.dumps({"type": "user.created", "data": {}})
        headers = _signed_headers(payload)
        headers["svix-signature"] = "v1,dGFtcGVyZWQ="
        with patch("src.api.routes.clerk_webhooks.get_settings", return_value=_settings()):
            r = _post(TestClient(_app()), {"type": "user.created", "data": {}}, headers=headers)
        assert r.status_code == 400

    def test_tampered_body_rejected(self):
        original = json.dumps({"type": "user.created", "data": {"id": "user_1"}})
        headers = _signed_headers(original)
        tampered = {"type": "user.created", "data": {"id": "user_EVIL"}}
        with patch("src.api.routes.clerk_webhooks.get_settings", return_value=_settings()):
            r = _post(TestClient(_app()), tampered, headers=headers)
        assert r.status_code == 400

    def test_missing_secret_returns_503(self):
        with patch("src.api.routes.clerk_webhooks.get_settings", return_value=_settings("")):
            r = _post(TestClient(_app()), {"type": "user.created", "data": {}})
        assert r.status_code == 503


def _user_event(event_type: str = "user.created", **data_overrides) -> dict:
    data = {
        "id": "user_2abc",
        "first_name": "Alice",
        "last_name": "Ng",
        "primary_email_address_id": "em_1",
        "email_addresses": [{"id": "em_1", "email_address": "alice@example.com"}],
        "public_metadata": {"role": "member"},
    }
    data.update(data_overrides)
    return {"type": event_type, "data": data}


class TestUserEvents:
    def _user(self, **overrides):
        base = {
            "id": uuid4(),
            "email": "alice@example.com",
            "full_name": "Alice Ng",
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_user_created_upserts_by_clerk_id(self):
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=None)
        pool.execute = AsyncMock()
        upsert = AsyncMock(return_value=self._user())
        with (
            patch("src.api.routes.clerk_webhooks.get_settings", return_value=_settings()),
            patch(
                "src.api.routes.clerk_webhooks.get_pool",
                new_callable=AsyncMock,
                return_value=pool,
            ),
            patch("src.api.routes.clerk_webhooks.get_or_create_by_clerk_id", upsert),
        ):
            r = _post(TestClient(_app()), _user_event())
        assert r.status_code == 200
        assert upsert.call_args.args[1] == "user_2abc"
        assert upsert.call_args.args[2] == "alice@example.com"

    def test_duplicate_delivery_is_idempotent(self):
        """Svix retries: the same event delivered twice must succeed twice."""
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=None)
        pool.execute = AsyncMock()
        upsert = AsyncMock(return_value=self._user())
        with (
            patch("src.api.routes.clerk_webhooks.get_settings", return_value=_settings()),
            patch(
                "src.api.routes.clerk_webhooks.get_pool",
                new_callable=AsyncMock,
                return_value=pool,
            ),
            patch("src.api.routes.clerk_webhooks.get_or_create_by_clerk_id", upsert),
        ):
            client = TestClient(_app())
            assert _post(client, _user_event()).status_code == 200
            assert _post(client, _user_event()).status_code == 200
        assert upsert.call_count == 2

    def test_super_admin_metadata_maps_to_is_admin(self):
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=None)
        pool.execute = AsyncMock()
        upsert = AsyncMock(return_value=self._user())
        event = _user_event(public_metadata={"role": "super_admin"})
        with (
            patch("src.api.routes.clerk_webhooks.get_settings", return_value=_settings()),
            patch(
                "src.api.routes.clerk_webhooks.get_pool",
                new_callable=AsyncMock,
                return_value=pool,
            ),
            patch("src.api.routes.clerk_webhooks.get_or_create_by_clerk_id", upsert),
        ):
            _post(TestClient(_app()), event)
        assert upsert.call_args.args[3] is True  # is_admin

    def test_user_deleted_soft_deactivates(self):
        pool = MagicMock()
        pool.execute = AsyncMock()
        with (
            patch("src.api.routes.clerk_webhooks.get_settings", return_value=_settings()),
            patch(
                "src.api.routes.clerk_webhooks.get_pool",
                new_callable=AsyncMock,
                return_value=pool,
            ),
        ):
            r = _post(TestClient(_app()), {"type": "user.deleted", "data": {"id": "user_2abc"}})
        assert r.status_code == 200
        sql = pool.execute.call_args.args[0]
        assert "is_active = FALSE" in sql
        assert "DELETE" not in sql.upper()


class TestOrgEvents:
    def test_organization_created_upserts(self):
        org = SimpleNamespace(id=uuid4(), name="Acme")
        upsert = AsyncMock(return_value=org)
        pool = MagicMock()
        pool.execute = AsyncMock()
        with (
            patch("src.api.routes.clerk_webhooks.get_settings", return_value=_settings()),
            patch(
                "src.api.routes.clerk_webhooks.get_pool",
                new_callable=AsyncMock,
                return_value=pool,
            ),
            patch("src.api.routes.clerk_webhooks.get_or_create_org_by_clerk_id", upsert),
        ):
            r = _post(
                TestClient(_app()),
                {"type": "organization.created", "data": {"id": "org_9", "name": "Acme"}},
            )
        assert r.status_code == 200
        upsert.assert_awaited_once()
        assert upsert.call_args.args[1] == "org_9"

    def test_organization_deleted_detaches_link_only(self):
        pool = MagicMock()
        pool.execute = AsyncMock()
        with (
            patch("src.api.routes.clerk_webhooks.get_settings", return_value=_settings()),
            patch(
                "src.api.routes.clerk_webhooks.get_pool",
                new_callable=AsyncMock,
                return_value=pool,
            ),
        ):
            r = _post(TestClient(_app()), {"type": "organization.deleted", "data": {"id": "org_9"}})
        assert r.status_code == 200
        sql = pool.execute.call_args.args[0]
        assert "clerk_org_id = NULL" in sql
        assert "DELETE" not in sql.upper()


class TestMembershipEvents:
    def _event(self, event_type: str, role: str = "org:admin") -> dict:
        return {
            "type": event_type,
            "data": {
                "organization": {"id": "org_9", "name": "Acme"},
                "public_user_data": {"user_id": "user_2abc"},
                "role": role,
            },
        }

    def test_membership_created_moves_user_and_sets_role(self):
        org = SimpleNamespace(id=uuid4(), name="Acme")
        user_row = {"id": uuid4(), "org_id": uuid4()}
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=user_row)
        pool.execute = AsyncMock()
        with (
            patch("src.api.routes.clerk_webhooks.get_settings", return_value=_settings()),
            patch(
                "src.api.routes.clerk_webhooks.get_pool",
                new_callable=AsyncMock,
                return_value=pool,
            ),
            patch(
                "src.api.routes.clerk_webhooks.get_or_create_org_by_clerk_id",
                AsyncMock(return_value=org),
            ),
        ):
            r = _post(TestClient(_app()), self._event("organizationMembership.created"))
        assert r.status_code == 200
        args = pool.execute.call_args.args
        assert args[1] == org.id
        assert args[2] == "org_owner"  # org:admin → org_owner

    def test_membership_deleted_moves_user_back_to_default_org(self):
        org_id = uuid4()
        default_org = uuid4()
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"id": uuid4(), "org_id": org_id})
        pool.fetchval = AsyncMock(return_value=default_org)
        pool.execute = AsyncMock()
        with (
            patch("src.api.routes.clerk_webhooks.get_settings", return_value=_settings()),
            patch(
                "src.api.routes.clerk_webhooks.get_pool",
                new_callable=AsyncMock,
                return_value=pool,
            ),
            patch(
                "src.api.routes.clerk_webhooks.get_org_by_clerk_id",
                AsyncMock(return_value=SimpleNamespace(id=org_id)),
            ),
        ):
            r = _post(TestClient(_app()), self._event("organizationMembership.deleted"))
        assert r.status_code == 200
        args = pool.execute.call_args.args
        assert args[1] == default_org

    def test_membership_for_unknown_user_is_a_noop(self):
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        pool.execute = AsyncMock()
        with (
            patch("src.api.routes.clerk_webhooks.get_settings", return_value=_settings()),
            patch(
                "src.api.routes.clerk_webhooks.get_pool",
                new_callable=AsyncMock,
                return_value=pool,
            ),
        ):
            r = _post(TestClient(_app()), self._event("organizationMembership.created"))
        assert r.status_code == 200
        pool.execute.assert_not_awaited()
