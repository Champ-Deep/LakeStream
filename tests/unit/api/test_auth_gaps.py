"""Regression tests for the C1 auth-gap closures.

1. GET /api/jobs/{job_id} (jobs_alias) used to be completely unauthenticated —
   any UUID leaked job status + export links. It now requires auth and 404s
   on cross-tenant access.
2. GET|PATCH /api/settings/ used to silently fall back to the default org when
   unauthenticated (PATCH writes openrouter_api_key). It now 401s.
3. The web /signup routes are closed under auth_provider=clerk.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.models.job import ScrapeJob


def _build_app(router, *, state: dict | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")

    @app.middleware("http")
    async def _inject_state(request: Request, call_next):
        for k, v in (state or {}).items():
            setattr(request.state, k, v)
        return await call_next(request)

    return app


def _fake_job(*, org_id: str | None, user_id: str | None) -> ScrapeJob:
    return ScrapeJob(
        id=uuid4(),
        domain="example.com",
        template_id="default",
        status="completed",
        org_id=UUID(org_id) if org_id else None,
        user_id=UUID(user_id) if user_id else None,
        strategy_used="http",
        cost_usd=0.0,
        duration_ms=100,
        pages_scraped=1,
        created_at=datetime.now(UTC),
        completed_at=None,
        error_message=None,
        retry_count=0,
    )


ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"
USER = "33333333-3333-3333-3333-333333333333"


class TestJobsAliasAuth:
    def _patches(self, job):
        return (
            patch(
                "src.api.routes.jobs_alias.get_pool",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch(
                "src.api.routes.jobs_alias.job_queries.get_job",
                new_callable=AsyncMock,
                return_value=job,
            ),
            patch(
                "src.api.routes.jobs_alias.data_queries.count_scraped_data_by_job",
                new_callable=AsyncMock,
                return_value=2,
            ),
        )

    def test_unauthenticated_returns_401(self):
        from src.api.routes.jobs_alias import router

        client = TestClient(_build_app(router, state=None))
        r = client.get(f"/api/jobs/{uuid4()}")
        assert r.status_code == 401

    def test_cross_org_returns_404(self):
        from src.api.routes.jobs_alias import router

        job = _fake_job(org_id=ORG_A, user_id=None)
        p1, p2, p3 = self._patches(job)
        with p1, p2, p3:
            client = TestClient(
                _build_app(router, state={"org_id": ORG_B, "user_id": USER, "is_admin": False})
            )
            r = client.get(f"/api/jobs/{job.id}")
        assert r.status_code == 404

    def test_same_org_returns_200(self):
        from src.api.routes.jobs_alias import router

        job = _fake_job(org_id=ORG_A, user_id=USER)
        p1, p2, p3 = self._patches(job)
        with p1, p2, p3:
            client = TestClient(
                _build_app(router, state={"org_id": ORG_A, "user_id": USER, "is_admin": False})
            )
            r = client.get(f"/api/jobs/{job.id}")
        assert r.status_code == 200
        assert r.json()["data_count"] == 2

    def test_admin_sees_other_org(self):
        from src.api.routes.jobs_alias import router

        job = _fake_job(org_id=ORG_A, user_id=None)
        p1, p2, p3 = self._patches(job)
        with p1, p2, p3:
            client = TestClient(
                _build_app(router, state={"org_id": ORG_B, "user_id": USER, "is_admin": True})
            )
            r = client.get(f"/api/jobs/{job.id}")
        assert r.status_code == 200


class TestSettingsAuth:
    def test_get_unauthenticated_returns_401(self):
        from src.api.routes.settings import router

        client = TestClient(_build_app(router, state=None))
        r = client.get("/api/settings/")
        assert r.status_code == 401

    def test_patch_unauthenticated_returns_401(self):
        from src.api.routes.settings import router

        client = TestClient(_build_app(router, state=None))
        r = client.patch("/api/settings/", json={"openrouter_api_key": "sk-steal-me"})
        assert r.status_code == 401

    def test_get_scoped_to_caller_org(self):
        from src.api.routes.settings import router

        pool = MagicMock()
        pool.fetchrow = AsyncMock(
            return_value={
                "proxy_url": "",
                "webhook_url": "",
                "webhook_auto_send": False,
                "webhook_include_metadata": False,
                "openrouter_api_key": "",
                "llm_model": None,
            }
        )
        with patch("src.api.routes.settings.get_pool", new_callable=AsyncMock, return_value=pool):
            client = TestClient(
                _build_app(router, state={"org_id": ORG_A, "user_id": USER, "is_admin": False})
            )
            r = client.get("/api/settings/")
        assert r.status_code == 200
        # The query must have been issued with the caller's org, never a default
        assert pool.fetchrow.call_args.args[-1] == ORG_A


class TestSignupClosedUnderClerk:
    def _clerk_settings(self):
        return SimpleNamespace(
            auth_provider="clerk",
            clerk_publishable_key="pk_test_x",
            access_token_expire_hours=24,
        )

    def test_get_signup_redirects_to_login(self):
        from src.api.routes.web.auth import router

        app = FastAPI()
        app.include_router(router)
        with patch("src.config.settings.get_settings", return_value=self._clerk_settings()):
            client = TestClient(app)
            r = client.get("/signup", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/login"

    def test_post_signup_returns_410(self):
        from src.api.routes.web.auth import router

        app = FastAPI()
        app.include_router(router)
        with patch("src.config.settings.get_settings", return_value=self._clerk_settings()):
            client = TestClient(app)
            r = client.post(
                "/signup",
                data={"email": "x@y.co", "password": "hunter2!", "full_name": "X", "org_name": "Y"},
            )
        assert r.status_code == 410

    def test_post_login_returns_410(self):
        from src.api.routes.web.auth import router

        app = FastAPI()
        app.include_router(router)
        with patch("src.config.settings.get_settings", return_value=self._clerk_settings()):
            client = TestClient(app)
            r = client.post("/login", data={"email": "x@y.co", "password": "hunter2!"})
        assert r.status_code == 410
