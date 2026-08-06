"""Tests for scripts/clerk_import_users.py.

Clerk API calls go through httpx.MockTransport — no network. The DB pool is a
mock; what matters here is the decision logic: payload construction, the
existing-email 422 backfill, dry-run behavior, and collision handling.
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx

_SCRIPT = Path(__file__).parents[3] / "scripts" / "clerk_import_users.py"
_spec = importlib.util.spec_from_file_location("clerk_import_users", _SCRIPT)
ciu = importlib.util.module_from_spec(_spec)
sys.modules["clerk_import_users"] = ciu
_spec.loader.exec_module(ciu)

BCRYPT_HASH = "$2b$12$abcdefghijklmnopqrstuvC4y8mBSrv0v0DHhIGkAdLLZo8f5C24S"


def _user(**overrides) -> dict:
    base = {
        "id": uuid4(),
        "email": "alice@example.com",
        "password_hash": BCRYPT_HASH,
        "full_name": "Alice Ng",
        "role": "org_owner",
        "is_admin": False,
        "is_active": True,
        "org_id": uuid4(),
    }
    base.update(overrides)
    return base


class TestBuildUserPayload:
    def test_bcrypt_hash_carries_over(self):
        payload, note = ciu.build_user_payload(_user())
        assert payload["password_hasher"] == "bcrypt"
        assert payload["password_digest"] == BCRYPT_HASH
        assert payload["skip_password_checks"] is True
        assert note == ""
        assert payload["first_name"] == "Alice"
        assert payload["last_name"] == "Ng"

    def test_empty_hash_imports_passwordless(self):
        payload, note = ciu.build_user_payload(_user(password_hash=""))
        assert payload["skip_password_requirement"] is True
        assert "password_digest" not in payload
        assert note == "needs-password-reset"

    def test_placeholder_hash_imports_passwordless(self):
        payload, note = ciu.build_user_payload(
            _user(password_hash="REPLACE_WITH_BCRYPT_HASH")
        )
        assert payload["skip_password_requirement"] is True
        assert note == "needs-password-reset"

    def test_non_bcrypt_hash_flagged(self):
        payload, note = ciu.build_user_payload(_user(password_hash="pbkdf2$something"))
        assert payload["skip_password_requirement"] is True
        assert "non-bcrypt" in note

    def test_super_admin_metadata(self):
        payload, _ = ciu.build_user_payload(_user(is_admin=True))
        assert payload["public_metadata"]["role"] == "super_admin"

    def test_role_and_local_org_pinned_in_metadata(self):
        user = _user()
        payload, _ = ciu.build_user_payload(user)
        assert payload["public_metadata"]["role"] == "org_owner"
        assert payload["public_metadata"]["org_id"] == str(user["org_id"])


class TestEmailCollisions:
    def test_case_collision_detected(self):
        users = [_user(email="Alice@Example.com"), _user(email="alice@example.com")]
        assert len(ciu.check_email_collisions(users)) == 1

    def test_distinct_emails_pass(self):
        users = [_user(email="a@x.co"), _user(email="b@x.co")]
        assert ciu.check_email_collisions(users) == []


def _pool_with_users(users: list[dict]):
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=users)
    pool.execute = AsyncMock()
    return pool


def _clerk_with_responses(handler) -> "ciu.ClerkClient":
    return ciu.ClerkClient("sk_test", transport=httpx.MockTransport(handler))


class TestImportUsers:
    def _run(self, pool, clerk, *, execute=True):
        import asyncio

        report: list[dict] = []
        with patch.object(ciu.time, "sleep"):
            asyncio.run(
                ciu.import_users(
                    pool, clerk, execute=execute, include_inactive=False, report=report
                )
            )
        return report

    def test_created_user_backfills_clerk_id(self):
        user = _user()

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/users"
            body = json.loads(request.content)
            assert body["password_hasher"] == "bcrypt"
            return httpx.Response(200, json={"id": "user_new1"})

        pool = _pool_with_users([user])
        report = self._run(pool, _clerk_with_responses(handler))
        assert report[0]["action"] == "created"
        args = pool.execute.call_args.args
        assert args[1] == "user_new1"
        assert args[2] == user["id"]

    def test_existing_email_422_links_instead(self):
        user = _user()
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            if request.method == "POST":
                return httpx.Response(
                    422, json={"errors": [{"message": "That email address is taken."}]}
                )
            return httpx.Response(200, json=[{"id": "user_existing"}])

        pool = _pool_with_users([user])
        report = self._run(pool, _clerk_with_responses(handler))
        assert report[0]["action"] == "linked-existing"
        assert pool.execute.call_args.args[1] == "user_existing"
        assert ("GET", "/v1/users") in calls

    def test_api_failure_reported_not_written(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        pool = _pool_with_users([_user()])
        report = self._run(pool, _clerk_with_responses(handler))
        assert report[0]["action"] == "FAILED"
        pool.execute.assert_not_awaited()

    def test_dry_run_makes_no_calls_and_no_writes(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("dry run must not call Clerk")

        pool = _pool_with_users([_user()])
        report = self._run(pool, _clerk_with_responses(handler), execute=False)
        assert report[0]["action"] == "would-create"
        pool.execute.assert_not_awaited()

    def test_case_collisions_skipped(self):
        users = [_user(email="Alice@Example.com"), _user(email="alice@example.com")]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"id": f"user_{request.url.path[-1]}"})

        pool = _pool_with_users(users)
        report = self._run(pool, _clerk_with_responses(handler))
        skipped = [r for r in report if r["action"] == "SKIPPED"]
        assert len(skipped) == 1
        assert "case-collision" in skipped[0]["note"]


class TestImportMemberships:
    def test_role_mapping(self):
        rows = [
            {"email": "o@x.co", "role": "org_owner", "clerk_user_id": "u1", "clerk_org_id": "org1"},
            {"email": "m@x.co", "role": "member", "clerk_user_id": "u2", "clerk_org_id": "org1"},
        ]
        sent = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "mem_1"})

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=rows)
        import asyncio

        report: list[dict] = []
        with patch.object(ciu.time, "sleep"):
            asyncio.run(
                ciu.import_memberships(
                    pool, _clerk_with_responses(handler), execute=True, report=report
                )
            )
        assert sent[0]["role"] == "org:admin"
        assert sent[1]["role"] == "org:member"

    def test_already_member_422_is_idempotent(self):
        rows = [{"email": "o@x.co", "role": "member", "clerk_user_id": "u1", "clerk_org_id": "org1"}]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"errors": [{"message": "already a member"}]})

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=rows)
        import asyncio

        report: list[dict] = []
        with patch.object(ciu.time, "sleep"):
            asyncio.run(
                ciu.import_memberships(
                    pool, _clerk_with_responses(handler), execute=True, report=report
                )
            )
        assert report[0]["action"] == "already-member"


class TestImportOrgs:
    def test_org_created_and_backfilled(self):
        org_id = uuid4()

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["private_metadata"]["local_org_id"] == str(org_id)
            return httpx.Response(200, json={"id": "org_new1"})

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[{"id": org_id, "name": "Acme", "slug": "acme"}])
        pool.execute = AsyncMock()
        import asyncio

        report: list[dict] = []
        with patch.object(ciu.time, "sleep"):
            asyncio.run(
                ciu.import_orgs(pool, _clerk_with_responses(handler), execute=True, report=report)
            )
        assert report[0]["action"] == "created"
        assert pool.execute.call_args.args[1] == "org_new1"
