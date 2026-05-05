"""Regression tests for plan.md S1.2 — implement webhook callback (CRIT-3).

Before this commit, POST /api/webhook/callback/{job_id} accepted any
JSON body and returned `{received_keys: [...]}` without persisting
anything. The PRD's n8n integration assumed this endpoint stored
enrichment results back in scraped_data.

These tests verify:
  - Unauthenticated callers get 401.
  - Cross-org callbacks get 404 (so n8n credentials from one tenant
    can't write into another tenant's job).
  - Same-org callbacks 200 and persist to scraped_data.
  - Oversized payloads are rejected with 413.
  - Well-known top-level fields (url, title, source) are extracted
    out of the payload for indexing.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


def _build_app(state: dict | None = None) -> FastAPI:
    """Mount the webhook router with optional request.state injection."""
    from src.api.routes.webhook import router

    app = FastAPI()
    app.include_router(router, prefix="/api")

    @app.middleware("http")
    async def _inject_state(request: Request, call_next):
        for k, v in (state or {}).items():
            setattr(request.state, k, v)
        return await call_next(request)

    return app


JOB_ORG = "11111111-1111-1111-1111-111111111111"
OTHER_ORG = "22222222-2222-2222-2222-222222222222"
USER = "33333333-3333-3333-3333-333333333333"


class TestWebhookCallbackAuth:
    def test_unauthenticated_returns_401(self):
        app = _build_app(state=None)
        client = TestClient(app)
        r = client.post(f"/api/webhook/callback/{uuid4()}", json={"foo": "bar"})
        assert r.status_code == 401

    def test_cross_org_returns_404(self):
        job_id = uuid4()
        fake_pool = MagicMock()
        fake_pool.fetchrow = AsyncMock(
            return_value={
                "domain": "example.com",
                "org_id": UUID(JOB_ORG),
                "user_id": None,
            }
        )
        with (
            patch("src.db.pool.get_pool", new_callable=AsyncMock) as mock_pool,
            patch(
                "src.db.queries.scraped_data.insert_scraped_data",
                new_callable=AsyncMock,
            ) as mock_insert,
        ):
            mock_pool.return_value = fake_pool
            app = _build_app(
                state={"org_id": OTHER_ORG, "user_id": USER, "is_admin": False}
            )
            client = TestClient(app)
            r = client.post(f"/api/webhook/callback/{job_id}", json={"foo": "bar"})
            assert r.status_code == 404
            mock_insert.assert_not_called()

    def test_missing_job_returns_404(self):
        fake_pool = MagicMock()
        fake_pool.fetchrow = AsyncMock(return_value=None)
        with (
            patch("src.db.pool.get_pool", new_callable=AsyncMock) as mock_pool,
            patch(
                "src.db.queries.scraped_data.insert_scraped_data",
                new_callable=AsyncMock,
            ) as mock_insert,
        ):
            mock_pool.return_value = fake_pool
            app = _build_app(
                state={"org_id": JOB_ORG, "user_id": USER, "is_admin": False}
            )
            client = TestClient(app)
            r = client.post(f"/api/webhook/callback/{uuid4()}", json={"foo": "bar"})
            assert r.status_code == 404
            mock_insert.assert_not_called()


class TestWebhookCallbackPersistence:
    def _mocked_pool(self, *, org_id=JOB_ORG, user_id=None):
        pool = MagicMock()
        pool.fetchrow = AsyncMock(
            return_value={
                "domain": "example.com",
                "org_id": UUID(org_id),
                "user_id": UUID(user_id) if user_id else None,
            }
        )
        return pool

    def test_same_org_persists_payload(self):
        new_record_id = uuid4()
        with (
            patch("src.db.pool.get_pool", new_callable=AsyncMock) as mock_pool,
            patch(
                "src.db.queries.scraped_data.insert_scraped_data",
                new_callable=AsyncMock,
                return_value=new_record_id,
            ) as mock_insert,
        ):
            mock_pool.return_value = self._mocked_pool()
            app = _build_app(
                state={"org_id": JOB_ORG, "user_id": USER, "is_admin": False}
            )
            client = TestClient(app)

            payload = {
                "source": "n8n",
                "url": "https://example.com/enriched",
                "title": "Enriched record",
                "score": 0.92,
                "tags": ["high-intent", "enterprise"],
            }
            r = client.post(f"/api/webhook/callback/{uuid4()}", json=payload)
            assert r.status_code == 200
            body = r.json()
            assert body["success"] is True
            assert body["record_id"] == str(new_record_id)

            # Verify the call into the insert: well-known fields lifted to
            # columns, full payload preserved under metadata.payload, and
            # the row inherits the job's org_id (not the caller's — these
            # happen to match here, but the row scope must follow the job).
            kwargs = mock_insert.call_args.kwargs
            assert kwargs["data_type"] == "webhook_callback"
            assert kwargs["url"] == "https://example.com/enriched"
            assert kwargs["title"] == "Enriched record"
            assert kwargs["domain"] == "example.com"
            assert str(kwargs["org_id"]) == JOB_ORG
            assert kwargs["metadata"]["source"] == "n8n"
            assert kwargs["metadata"]["payload"] == payload

    def test_admin_can_post_to_any_orgs_job(self):
        with (
            patch("src.db.pool.get_pool", new_callable=AsyncMock) as mock_pool,
            patch(
                "src.db.queries.scraped_data.insert_scraped_data",
                new_callable=AsyncMock,
                return_value=uuid4(),
            ),
        ):
            mock_pool.return_value = self._mocked_pool(org_id=JOB_ORG)
            app = _build_app(
                state={"org_id": OTHER_ORG, "user_id": USER, "is_admin": True}
            )
            client = TestClient(app)
            r = client.post(f"/api/webhook/callback/{uuid4()}", json={"foo": "bar"})
            assert r.status_code == 200

    def test_payload_without_well_known_fields_still_works(self):
        new_record_id = uuid4()
        with (
            patch("src.db.pool.get_pool", new_callable=AsyncMock) as mock_pool,
            patch(
                "src.db.queries.scraped_data.insert_scraped_data",
                new_callable=AsyncMock,
                return_value=new_record_id,
            ) as mock_insert,
        ):
            mock_pool.return_value = self._mocked_pool()
            app = _build_app(
                state={"org_id": JOB_ORG, "user_id": USER, "is_admin": False}
            )
            client = TestClient(app)

            payload = {"some_arbitrary_key": [1, 2, 3]}
            r = client.post(f"/api/webhook/callback/{uuid4()}", json=payload)
            assert r.status_code == 200

            kwargs = mock_insert.call_args.kwargs
            # No url/title in payload → columns are None
            assert kwargs["url"] is None
            assert kwargs["title"] is None
            # No source in payload → defaults to "webhook"
            assert kwargs["metadata"]["source"] == "webhook"
            assert kwargs["metadata"]["payload"] == payload


class TestWebhookCallbackPayloadCap:
    def test_oversized_payload_returns_413(self):
        # Build a payload bigger than the 256 KiB cap. Using a dict with one
        # huge string keeps the JSON encoding cheap.
        huge_value = "x" * (300 * 1024)
        payload = {"source": "n8n", "blob": huge_value}

        with (
            patch("src.db.pool.get_pool", new_callable=AsyncMock),
            patch(
                "src.db.queries.scraped_data.insert_scraped_data",
                new_callable=AsyncMock,
            ) as mock_insert,
        ):
            app = _build_app(
                state={"org_id": JOB_ORG, "user_id": USER, "is_admin": False}
            )
            client = TestClient(app)
            r = client.post(f"/api/webhook/callback/{uuid4()}", json=payload)
            assert r.status_code == 413
            mock_insert.assert_not_called()
            # Confirm the size really did cross the cap (sanity check on the
            # constant — keeps the test honest if the cap changes later).
            assert len(json.dumps(payload).encode()) > 256 * 1024
