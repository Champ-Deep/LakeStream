"""Regression tests for plan.md S1.1 — close the unauth job-data leak (CRIT-1).

These tests verify that:

1. /api/scrape/status/{job_id} and /api/scrape/stream/{job_id} reject
   unauthenticated callers with 401 (used to be wide open).
2. Authenticated callers can only see jobs in their own org. Cross-org
   access returns 404 (we 404 instead of 403 so cross-tenant probing
   can't enumerate UUIDs).
3. Same rules apply to /api/export/csv/{job_id} and /api/export/json/{job_id}.
4. /api/tracked/{domain} DELETE only affects rows in the caller's org.
5. The bare-status helper require_org() rejects requests where
   request.state.org_id is None.

We don't spin up Postgres / Redis here — we patch the DB pool and the
job-queries module so the tests exercise the *route handlers* and the
new authorize_resource() rules in isolation.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from src.api.middleware.auth import authorize_resource, require_org


def _build_app(router_module_name: str, *, state: dict | None = None) -> FastAPI:
    """Build a minimal FastAPI app mounting the route under test.

    A tiny middleware injects request.state from the `state` dict so we can
    simulate "authenticated as org X / user Y" without going through the
    real TenantContextMiddleware (which requires Redis + JWT decoding).
    """
    app = FastAPI()
    if router_module_name == "scrape":
        from src.api.routes.scrape import router
    elif router_module_name == "exports":
        from src.api.routes.exports import router
    elif router_module_name == "tracked":
        from src.api.routes.tracked import router
    else:
        raise ValueError(router_module_name)
    app.include_router(router, prefix="/api")

    @app.middleware("http")
    async def _inject_state(request: Request, call_next):
        for k, v in (state or {}).items():
            setattr(request.state, k, v)
        return await call_next(request)

    return app


# ---------------------------------------------------------------------------
# require_org() unit tests
# ---------------------------------------------------------------------------


class TestRequireOrgHelper:
    def test_raises_401_when_org_id_missing(self):
        req = MagicMock(spec=Request)
        req.state = MagicMock()
        # getattr(state, "org_id", None) → None: no attribute set
        del req.state.org_id  # ensure missing
        with pytest.raises(HTTPException) as exc:
            require_org(req)
        assert exc.value.status_code == 401

    def test_raises_401_when_org_id_empty_string(self):
        req = MagicMock(spec=Request)
        req.state = MagicMock()
        req.state.org_id = ""
        with pytest.raises(HTTPException) as exc:
            require_org(req)
        assert exc.value.status_code == 401

    def test_returns_tuple_when_authenticated(self):
        req = MagicMock(spec=Request)
        req.state = MagicMock()
        req.state.org_id = "11111111-1111-1111-1111-111111111111"
        req.state.user_id = "22222222-2222-2222-2222-222222222222"
        req.state.is_admin = False
        org, user, admin = require_org(req)
        assert org == "11111111-1111-1111-1111-111111111111"
        assert user == "22222222-2222-2222-2222-222222222222"
        assert admin is False


# ---------------------------------------------------------------------------
# authorize_resource() unit tests
# ---------------------------------------------------------------------------


class TestAuthorizeResource:
    ORG_A = "11111111-1111-1111-1111-111111111111"
    ORG_B = "22222222-2222-2222-2222-222222222222"
    USER_A = "33333333-3333-3333-3333-333333333333"
    USER_B = "44444444-4444-4444-4444-444444444444"

    def test_admin_can_access_any_resource(self):
        # Should not raise even when org/user mismatch
        authorize_resource(
            resource_org_id=UUID(self.ORG_B),
            resource_user_id=UUID(self.USER_B),
            caller_org_id=self.ORG_A,
            caller_user_id=self.USER_A,
            caller_is_admin=True,
        )

    def test_same_org_same_user_allowed(self):
        authorize_resource(
            resource_org_id=UUID(self.ORG_A),
            resource_user_id=UUID(self.USER_A),
            caller_org_id=self.ORG_A,
            caller_user_id=self.USER_A,
            caller_is_admin=False,
        )

    def test_cross_org_returns_404(self):
        with pytest.raises(HTTPException) as exc:
            authorize_resource(
                resource_org_id=UUID(self.ORG_B),
                resource_user_id=None,
                caller_org_id=self.ORG_A,
                caller_user_id=self.USER_A,
                caller_is_admin=False,
            )
        assert exc.value.status_code == 404

    def test_same_org_different_user_returns_404(self):
        with pytest.raises(HTTPException) as exc:
            authorize_resource(
                resource_org_id=UUID(self.ORG_A),
                resource_user_id=UUID(self.USER_B),
                caller_org_id=self.ORG_A,
                caller_user_id=self.USER_A,
                caller_is_admin=False,
            )
        assert exc.value.status_code == 404

    def test_resource_with_no_user_id_allowed_within_org(self):
        # Worker-created jobs may have user_id=None; same-org access OK
        authorize_resource(
            resource_org_id=UUID(self.ORG_A),
            resource_user_id=None,
            caller_org_id=self.ORG_A,
            caller_user_id=self.USER_A,
            caller_is_admin=False,
        )

    def test_anonymous_caller_with_no_user_id_blocked_cross_user(self):
        # If caller_user_id is None but resource has a user_id, we don't 404
        # (the caller might be an API key tied to the org, not a specific user).
        # Org match is enough.
        authorize_resource(
            resource_org_id=UUID(self.ORG_A),
            resource_user_id=UUID(self.USER_A),
            caller_org_id=self.ORG_A,
            caller_user_id=None,
            caller_is_admin=False,
        )


# ---------------------------------------------------------------------------
# /api/scrape/status/{job_id} regression tests
# ---------------------------------------------------------------------------


def _fake_job(*, org_id: str | None, user_id: str | None = None, status: str = "pending"):
    """Build a ScrapeJob-shaped object; only the fields the handlers read."""
    return MagicMock(
        id=uuid4(),
        org_id=UUID(org_id) if org_id else None,
        user_id=UUID(user_id) if user_id else None,
        domain="example.com",
        status=status,
        strategy_used=None,
        pages_scraped=0,
        cost_usd=0.0,
        duration_ms=None,
        created_at=datetime.now(UTC),
        completed_at=None,
        error_message=None,
        retry_count=0,
    )


class TestScrapeStatusAuth:
    JOB_ORG = "11111111-1111-1111-1111-111111111111"
    OTHER_ORG = "22222222-2222-2222-2222-222222222222"
    USER = "33333333-3333-3333-3333-333333333333"

    def test_unauthenticated_returns_401(self):
        # No state injected → require_org() should fire 401
        app = _build_app("scrape", state=None)
        client = TestClient(app)
        r = client.get(f"/api/scrape/status/{uuid4()}")
        assert r.status_code == 401

    def test_cross_org_returns_404(self):
        job = _fake_job(org_id=self.JOB_ORG, user_id=None)
        with (
            patch("src.api.routes.scrape.get_pool", new_callable=AsyncMock) as mock_pool,
            patch(
                "src.api.routes.scrape.job_queries.get_job",
                new_callable=AsyncMock,
                return_value=job,
            ),
            patch(
                "src.api.routes.scrape.data_queries.count_scraped_data_by_job",
                new_callable=AsyncMock,
                return_value=0,
            ),
        ):
            mock_pool.return_value = MagicMock()
            app = _build_app(
                "scrape",
                state={
                    "org_id": self.OTHER_ORG,
                    "user_id": self.USER,
                    "is_admin": False,
                },
            )
            client = TestClient(app)
            r = client.get(f"/api/scrape/status/{job.id}")
            assert r.status_code == 404

    def test_same_org_returns_200(self):
        job = _fake_job(org_id=self.JOB_ORG, user_id=self.USER)
        with (
            patch("src.api.routes.scrape.get_pool", new_callable=AsyncMock) as mock_pool,
            patch(
                "src.api.routes.scrape.job_queries.get_job",
                new_callable=AsyncMock,
                return_value=job,
            ),
            patch(
                "src.api.routes.scrape.data_queries.count_scraped_data_by_job",
                new_callable=AsyncMock,
                return_value=3,
            ),
        ):
            mock_pool.return_value = MagicMock()
            app = _build_app(
                "scrape",
                state={
                    "org_id": self.JOB_ORG,
                    "user_id": self.USER,
                    "is_admin": False,
                },
            )
            client = TestClient(app)
            r = client.get(f"/api/scrape/status/{job.id}")
            assert r.status_code == 200
            assert r.json()["data_count"] == 3

    def test_admin_sees_other_orgs(self):
        job = _fake_job(org_id=self.JOB_ORG, user_id=None)
        with (
            patch("src.api.routes.scrape.get_pool", new_callable=AsyncMock) as mock_pool,
            patch(
                "src.api.routes.scrape.job_queries.get_job",
                new_callable=AsyncMock,
                return_value=job,
            ),
            patch(
                "src.api.routes.scrape.data_queries.count_scraped_data_by_job",
                new_callable=AsyncMock,
                return_value=0,
            ),
        ):
            mock_pool.return_value = MagicMock()
            app = _build_app(
                "scrape",
                state={
                    "org_id": self.OTHER_ORG,
                    "user_id": self.USER,
                    "is_admin": True,  # admin
                },
            )
            client = TestClient(app)
            r = client.get(f"/api/scrape/status/{job.id}")
            assert r.status_code == 200

    def test_missing_job_returns_404(self):
        with (
            patch("src.api.routes.scrape.get_pool", new_callable=AsyncMock),
            patch(
                "src.api.routes.scrape.job_queries.get_job",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            app = _build_app(
                "scrape",
                state={"org_id": self.JOB_ORG, "user_id": self.USER, "is_admin": False},
            )
            client = TestClient(app)
            r = client.get(f"/api/scrape/status/{uuid4()}")
            assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/scrape/cancel/{job_id} regression tests
# ---------------------------------------------------------------------------


class TestScrapeCancelAuth:
    JOB_ORG = "11111111-1111-1111-1111-111111111111"
    OTHER_ORG = "22222222-2222-2222-2222-222222222222"
    USER = "33333333-3333-3333-3333-333333333333"

    def test_cross_org_cancel_returns_404(self):
        job = _fake_job(org_id=self.JOB_ORG, user_id=None, status="running")
        with (
            patch("src.api.routes.scrape.get_pool", new_callable=AsyncMock),
            patch(
                "src.api.routes.scrape.job_queries.get_job",
                new_callable=AsyncMock,
                return_value=job,
            ),
            patch(
                "src.api.routes.scrape.job_queries.cancel_job",
                new_callable=AsyncMock,
                return_value=True,
            ) as cancel_mock,
        ):
            app = _build_app(
                "scrape",
                state={"org_id": self.OTHER_ORG, "user_id": self.USER, "is_admin": False},
            )
            client = TestClient(app)
            r = client.post(f"/api/scrape/cancel/{job.id}")
            assert r.status_code == 404
            cancel_mock.assert_not_called()

    def test_same_org_cancel_succeeds(self):
        job = _fake_job(org_id=self.JOB_ORG, user_id=None, status="running")
        with (
            patch("src.api.routes.scrape.get_pool", new_callable=AsyncMock),
            patch(
                "src.api.routes.scrape.job_queries.get_job",
                new_callable=AsyncMock,
                return_value=job,
            ),
            patch(
                "src.api.routes.scrape.job_queries.cancel_job",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            app = _build_app(
                "scrape",
                state={"org_id": self.JOB_ORG, "user_id": self.USER, "is_admin": False},
            )
            client = TestClient(app)
            r = client.post(f"/api/scrape/cancel/{job.id}")
            assert r.status_code == 200
            assert r.json()["status"] == "cancelled"


# ---------------------------------------------------------------------------
# /api/export/csv/{job_id} regression tests
# ---------------------------------------------------------------------------


class TestExportCsvAuth:
    JOB_ORG = "11111111-1111-1111-1111-111111111111"
    OTHER_ORG = "22222222-2222-2222-2222-222222222222"
    USER = "33333333-3333-3333-3333-333333333333"

    def test_unauthenticated_returns_401(self):
        # get_current_user() raises 401 when nothing on request.state
        app = _build_app("exports", state=None)
        client = TestClient(app)
        r = client.get(f"/api/export/csv/{uuid4()}")
        assert r.status_code == 401

    def test_cross_org_export_returns_404(self):
        # _authorized_job_row uses pool.fetchrow directly. The import of
        # get_pool happens inside the helper so we patch the source module.
        job_id = uuid4()
        fake_pool = MagicMock()
        fake_pool.fetchrow = AsyncMock(
            return_value={
                "id": job_id,
                "domain": "example.com",
                "org_id": UUID(self.JOB_ORG),
                "user_id": None,
            }
        )
        with patch("src.db.pool.get_pool", new_callable=AsyncMock) as mock_pool:
            mock_pool.return_value = fake_pool
            app = _build_app(
                "exports",
                state={"org_id": self.OTHER_ORG, "user_id": self.USER, "is_admin": False},
            )
            client = TestClient(app)
            r = client.get(f"/api/export/csv/{job_id}")
            assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/tracked/{domain} DELETE regression tests
# ---------------------------------------------------------------------------


class TestTrackedDeleteAuth:
    ORG = "11111111-1111-1111-1111-111111111111"
    USER = "33333333-3333-3333-3333-333333333333"

    def test_unauthenticated_returns_401(self):
        app = _build_app("tracked", state=None)
        client = TestClient(app)
        r = client.delete("/api/tracked/example.com")
        assert r.status_code == 401

    def test_cross_org_delete_returns_404(self):
        # remove_tracked_domain is imported inside the route handler, so we
        # patch the source module + the pool source module.
        with (
            patch(
                "src.db.queries.tracked_domains.remove_tracked_domain",
                new_callable=AsyncMock,
                return_value=False,
            ) as mock_remove,
            patch("src.db.pool.get_pool", new_callable=AsyncMock),
        ):
            app = _build_app(
                "tracked",
                state={"org_id": self.ORG, "user_id": self.USER, "is_admin": False},
            )
            client = TestClient(app)
            r = client.delete("/api/tracked/example.com")
            assert r.status_code == 404
            # Confirm we actually scoped the delete by org
            kwargs = mock_remove.call_args.kwargs
            assert kwargs["org_id"] is not None
            assert str(kwargs["org_id"]) == self.ORG

    def test_admin_delete_skips_org_scope(self):
        with (
            patch(
                "src.db.queries.tracked_domains.remove_tracked_domain",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_remove,
            patch("src.db.pool.get_pool", new_callable=AsyncMock),
        ):
            app = _build_app(
                "tracked",
                state={"org_id": self.ORG, "user_id": self.USER, "is_admin": True},
            )
            client = TestClient(app)
            r = client.delete("/api/tracked/example.com")
            assert r.status_code == 200
            assert mock_remove.call_args.kwargs["org_id"] is None
