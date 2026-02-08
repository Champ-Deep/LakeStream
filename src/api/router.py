from fastapi import APIRouter

from src.api.routes import auth, domains, exports, health, scrape, signals, templates, tracked, webhook

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(scrape.router, tags=["scrape"])
api_router.include_router(domains.router, tags=["domains"])
api_router.include_router(templates.router, tags=["templates"])
api_router.include_router(exports.router)
api_router.include_router(webhook.router)
api_router.include_router(tracked.router)
api_router.include_router(signals.router)
