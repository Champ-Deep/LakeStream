"""Web UI routes for the LakeStream dashboard.

Split by resource into sibling modules — this package combines them into a
single `router` so the HTTP surface (paths, methods, response behavior) is
unchanged from when everything lived in one web.py file.
"""

from fastapi import APIRouter

from src.api.routes.web import (
    account,
    auth,
    dashboard,
    jobs,
    results,
    tech,
    users,
)

router = APIRouter(tags=["web"])

router.include_router(auth.router)
router.include_router(dashboard.router)
router.include_router(jobs.router)
router.include_router(results.router)
router.include_router(tech.router)
router.include_router(users.router)
router.include_router(account.router)
