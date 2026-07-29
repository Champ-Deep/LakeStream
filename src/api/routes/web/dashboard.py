"""Dashboard page and its HTMX partials."""


import structlog
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.api.routes.web._shared import (
    _get_user_filter,
    _require_login,
    get_templates,
)

logger = structlog.get_logger()
router = APIRouter(tags=["web"])


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect
    """Main dashboard page."""
    from src.db.pool import get_pool
    from src.templates.registry import list_templates

    pool = await get_pool()
    user_filter = _get_user_filter(request)

    # Get job stats — filtered by user for non-admins
    if user_filter:
        total_jobs = await pool.fetchval(
            "SELECT COUNT(*) FROM scrape_jobs WHERE user_id = $1", user_filter
        )
        running_jobs = await pool.fetchval(
            "SELECT COUNT(*) FROM scrape_jobs WHERE status = 'running' AND user_id = $1",
            user_filter,
        )
        success_rate = await pool.fetchval(
            """SELECT COALESCE(
                COUNT(*) FILTER (WHERE status = 'completed') * 100 / NULLIF(COUNT(*), 0),
                0
            ) FROM scrape_jobs WHERE user_id = $1""",
            user_filter,
        )
        total_domains = await pool.fetchval(
            "SELECT COUNT(DISTINCT domain) FROM scraped_data WHERE user_id = $1", user_filter
        )
    else:
        total_jobs = await pool.fetchval("SELECT COUNT(*) FROM scrape_jobs")
        running_jobs = await pool.fetchval(
            "SELECT COUNT(*) FROM scrape_jobs WHERE status = 'running'"
        )
        success_rate = await pool.fetchval(
            """SELECT COALESCE(
                COUNT(*) FILTER (WHERE status = 'completed') * 100 / NULLIF(COUNT(*), 0),
                0
            ) FROM scrape_jobs"""
        )
        total_domains = await pool.fetchval("SELECT COUNT(DISTINCT domain) FROM scraped_data")

    stats = {
        "total_jobs": total_jobs or 0,
        "running_jobs": running_jobs or 0,
        "success_rate": success_rate or 0,
        "total_domains": total_domains or 0,
    }

    # Templates for the Quick Start advanced options dropdown
    templates_list = list_templates()

    # Tracked domains health summary
    tracked_domains: list = []
    domain_health: dict = {}
    try:
        from src.db.queries.tracked_domains import list_tracked_domains

        tracked_domains = await list_tracked_domains(pool)
        if tracked_domains:
            from src.db.queries.domains import get_domain_metadata

            for td in tracked_domains[:5]:
                meta = await get_domain_metadata(pool, td.domain)
                if meta:
                    domain_health[td.domain] = {
                        "success_rate": meta.success_rate or 0,
                        "block_count": meta.block_count or 0,
                    }
    except Exception:
        pass  # tracked_domains table may not exist yet

    return get_templates().TemplateResponse(
        "pages/dashboard.html",
        {
            "request": request,
            "active_page": "dashboard",
            "stats": stats,
            "templates": templates_list,
            "tracked_domains": tracked_domains,
            "domain_health": domain_health,
        },
    )


@router.get("/partials/health", response_class=HTMLResponse)
async def health_partial(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect
    """Health status partial for HTMX polling."""
    from src.db.pool import get_pool

    pool = await get_pool()

    # Check database
    try:
        db_ok = await pool.fetchval("SELECT 1") == 1
        database = "connected" if db_ok else "disconnected"
    except Exception:
        database = "disconnected"

    # Check Redis (simplified - just check if we can import)
    try:
        from src.config.settings import get_settings

        settings = get_settings()
        redis_configured = bool(settings.redis_url)
        redis = "connected" if redis_configured else "disconnected"
    except Exception:
        redis = "disconnected"

    health = {
        "status": "ok" if database == "connected" else "degraded",
        "database": database,
        "redis": redis,
    }

    return get_templates().TemplateResponse(
        "partials/health.html", {"request": request, "health": health}
    )


@router.get("/partials/recent-jobs", response_class=HTMLResponse)
async def recent_jobs_partial(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect
    """Recent jobs partial for HTMX polling."""
    from src.db.pool import get_pool
    from src.db.queries.jobs import list_jobs

    pool = await get_pool()
    user_filter = _get_user_filter(request)
    jobs = await list_jobs(pool, user_id=user_filter, limit=5, offset=0)

    return get_templates().TemplateResponse(
        "partials/recent_jobs.html", {"request": request, "jobs": jobs}
    )


@router.get("/partials/top-domains", response_class=HTMLResponse)
async def top_domains_partial(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect
    """Top domains partial for dashboard."""
    from src.db.pool import get_pool
    from src.db.queries.domains import list_domains

    pool = await get_pool()
    domains = await list_domains(pool, limit=5, sort_by="success_rate")

    return get_templates().TemplateResponse(
        "partials/top_domains.html", {"request": request, "domains": domains}
    )
