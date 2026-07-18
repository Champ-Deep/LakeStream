from fastapi import APIRouter

from src.api.routes import (
    api_keys,
    auth,
    discover,
    domains,
    enrich,
    exports,
    graph,
    health,
    ingest,
    jobs_alias,
    parse,
    scrape,
    screenshots,
    search,
    settings,
    signals,
    templates,
    tracked,
    usage,
    webhook,
)

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(api_keys.router, tags=["auth"])
api_router.include_router(scrape.router, tags=["scrape"])
api_router.include_router(jobs_alias.router, tags=["scrape"])
api_router.include_router(parse.router, tags=["parse"])
api_router.include_router(search.router, tags=["search"])
api_router.include_router(enrich.router, tags=["enrich"])
api_router.include_router(usage.router, tags=["usage"])
api_router.include_router(screenshots.router, tags=["screenshots"])
api_router.include_router(ingest.router, tags=["ingest"])
api_router.include_router(discover.router)
api_router.include_router(domains.router, tags=["domains"])
api_router.include_router(graph.router, tags=["graph"])
api_router.include_router(templates.router, tags=["templates"])
api_router.include_router(exports.router)
api_router.include_router(webhook.router)
api_router.include_router(tracked.router)
api_router.include_router(settings.router)
api_router.include_router(signals.router)
