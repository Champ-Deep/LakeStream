"""Web UI routes for the Lake B2B Scraper dashboard."""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["web"])


def get_templates():
    """Get templates instance from app state."""
    from src.server import templates

    return templates


# =============================================================================
# DASHBOARD
# =============================================================================


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page."""
    from src.db.pool import get_pool
    from src.templates.registry import list_templates

    pool = await get_pool()

    # Get job stats
    total_jobs = await pool.fetchval("SELECT COUNT(*) FROM scrape_jobs")
    running_jobs = await pool.fetchval(
        "SELECT COUNT(*) FROM scrape_jobs WHERE status = 'running'"
    )
    total_data = await pool.fetchval("SELECT COUNT(*) FROM scraped_data")
    total_cost = await pool.fetchval("SELECT COALESCE(SUM(cost_usd), 0) FROM scrape_jobs")

    stats = {
        "total_jobs": total_jobs or 0,
        "running_jobs": running_jobs or 0,
        "total_data": total_data or 0,
        "total_cost": float(total_cost or 0),
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


# =============================================================================
# PARTIALS (HTMX fragments)
# =============================================================================


@router.get("/partials/health", response_class=HTMLResponse)
async def health_partial(request: Request):
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
    """Recent jobs partial for HTMX polling."""
    from src.db.pool import get_pool
    from src.db.queries.jobs import list_jobs

    pool = await get_pool()
    jobs = await list_jobs(pool, limit=5, offset=0)

    return get_templates().TemplateResponse(
        "partials/recent_jobs.html", {"request": request, "jobs": jobs}
    )


@router.get("/partials/top-domains", response_class=HTMLResponse)
async def top_domains_partial(request: Request):
    """Top domains partial for dashboard."""
    from src.db.pool import get_pool
    from src.db.queries.domains import list_domains

    pool = await get_pool()
    domains = await list_domains(pool, limit=5, sort_by="success_rate")

    return get_templates().TemplateResponse(
        "partials/top_domains.html", {"request": request, "domains": domains}
    )


# =============================================================================
# JOBS PAGES
# =============================================================================


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_list(request: Request, status: str | None = None):
    """List all jobs page."""
    from src.db.pool import get_pool
    from src.db.queries.jobs import list_jobs

    pool = await get_pool()
    jobs = await list_jobs(pool, status=status, limit=50)

    return get_templates().TemplateResponse(
        "pages/jobs/list.html",
        {"request": request, "active_page": "jobs", "jobs": jobs, "filter_status": status},
    )


@router.get("/jobs/new", response_class=HTMLResponse)
async def new_job_form(request: Request):
    """Redirect to dashboard — scrape form is now integrated there."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/", status_code=302)


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_status_page(request: Request, job_id: UUID):
    """Job status detail page."""
    from src.db.pool import get_pool
    from src.db.queries.jobs import get_job
    from src.db.queries.scraped_data import count_scraped_data_by_job

    pool = await get_pool()
    job = await get_job(pool, job_id)

    if not job:
        return get_templates().TemplateResponse(
            "pages/jobs/not_found.html",
            {"request": request, "active_page": "jobs", "job_id": job_id},
            status_code=404,
        )

    data_count = await count_scraped_data_by_job(pool, job_id)

    return get_templates().TemplateResponse(
        "pages/jobs/status.html",
        {"request": request, "active_page": "jobs", "job": job, "data_count": data_count},
    )


@router.get("/partials/job/{job_id}/status", response_class=HTMLResponse)
async def job_status_partial(request: Request, job_id: UUID):
    """Job status partial for HTMX polling."""
    from src.db.pool import get_pool
    from src.db.queries.jobs import get_job
    from src.db.queries.scraped_data import count_scraped_data_by_job

    pool = await get_pool()
    job = await get_job(pool, job_id)
    data_count = await count_scraped_data_by_job(pool, job_id) if job else 0

    return get_templates().TemplateResponse(
        "partials/job_status.html",
        {"request": request, "job": job, "data_count": data_count},
    )


# =============================================================================
# RESULTS PAGES
# =============================================================================


@router.get("/results", response_class=HTMLResponse)
async def results_browse(
    request: Request,
    domain: str | None = None,
    data_type: str | None = None,
    page: int = 1,
):
    """Results browser page."""
    from src.db.pool import get_pool
    from src.db.queries.scraped_data import get_scraped_data_by_domain

    pool = await get_pool()
    limit = 50
    offset = (page - 1) * limit

    # Get unique domains for filter dropdown
    domains_rows = await pool.fetch("SELECT DISTINCT domain FROM scraped_data ORDER BY domain")
    domains = [row["domain"] for row in domains_rows]

    # Get results with filters
    if domain:
        results = await get_scraped_data_by_domain(pool, domain, data_type=data_type, limit=limit)
    else:
        query = "SELECT * FROM scraped_data ORDER BY scraped_at DESC LIMIT $1 OFFSET $2"
        rows = await pool.fetch(query, limit, offset)
        from src.db.queries.scraped_data import _parse_row

        results = [_parse_row(row) for row in rows]

    # Get total count
    total = await pool.fetchval("SELECT COUNT(*) FROM scraped_data")

    return get_templates().TemplateResponse(
        "pages/results/browse.html",
        {
            "request": request,
            "active_page": "results",
            "results": results,
            "domains": domains,
            "filter_domain": domain,
            "filter_data_type": data_type,
            "page": page,
            "total": total,
            "limit": limit,
        },
    )


# =============================================================================
# DOMAINS PAGES
# =============================================================================


@router.get("/domains", response_class=HTMLResponse)
async def domains_list(request: Request, sort_by: str = "last_scraped_at"):
    """Domains analytics page."""
    from src.db.pool import get_pool
    from src.db.queries.domains import list_domains

    pool = await get_pool()
    domains = await list_domains(pool, limit=50, sort_by=sort_by)

    # Fetch tracked domains for badges and the tracked sites section
    tracked_domains: list = []
    tracked_set: set = set()
    try:
        from src.db.queries.tracked_domains import list_tracked_domains

        tracked_domains = await list_tracked_domains(pool)
        tracked_set = {td.domain for td in tracked_domains}
    except Exception:
        pass  # tracked_domains table may not exist yet

    return get_templates().TemplateResponse(
        "pages/domains/list.html",
        {
            "request": request,
            "active_page": "domains",
            "domains": domains,
            "sort_by": sort_by,
            "tracked_domains": tracked_domains,
            "tracked_set": tracked_set,
        },
    )


@router.get("/domains/{domain}", response_class=HTMLResponse)
async def domain_detail(request: Request, domain: str):
    """Domain detail page."""
    from src.db.pool import get_pool
    from src.db.queries.domains import get_domain_metadata
    from src.db.queries.jobs import list_jobs

    pool = await get_pool()
    domain_meta = await get_domain_metadata(pool, domain)
    jobs = await list_jobs(pool, domain=domain, limit=10)

    # Get data type breakdown
    breakdown_rows = await pool.fetch(
        """
        SELECT data_type, COUNT(*) as count
        FROM scraped_data
        WHERE domain = $1
        GROUP BY data_type
        """,
        domain,
    )
    breakdown = {row["data_type"]: row["count"] for row in breakdown_rows}

    return get_templates().TemplateResponse(
        "pages/domains/detail.html",
        {
            "request": request,
            "active_page": "domains",
            "domain": domain,
            "metadata": domain_meta,
            "jobs": jobs,
            "breakdown": breakdown,
        },
    )


# =============================================================================
# HELP PAGES
# =============================================================================


@router.get("/help", response_class=HTMLResponse)
async def help_index(request: Request):
    """Help and documentation page."""
    return get_templates().TemplateResponse(
        "pages/help/index.html", {"request": request, "active_page": "help"}
    )


# =============================================================================
# SETTINGS PAGES
# =============================================================================


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Settings and webhook configuration page."""
    from src.config.settings import get_settings

    settings = get_settings()
    webhook_trigger_url = f"{settings.base_url}/api/webhook/trigger"

    return get_templates().TemplateResponse(
        "pages/settings/index.html",
        {
            "request": request,
            "active_page": "settings",
            "webhook_trigger_url": webhook_trigger_url,
        },
    )
