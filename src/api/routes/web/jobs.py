"""Jobs pages: list, bulk CSV upload, status detail, cancel/retry actions."""

from uuid import UUID

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.api.routes.web._shared import (
    _get_user_filter,
    _require_admin,
    _require_login,
    get_templates,
)

logger = structlog.get_logger()

router = APIRouter(tags=["web"])


# =============================================================================
# JOBS PAGES
# =============================================================================


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_list(request: Request, status: str | None = None, q: str | None = None):
    redirect = _require_login(request)
    if redirect:
        return redirect
    """List all jobs page."""
    from src.db.pool import get_pool
    from src.db.queries.jobs import list_jobs

    pool = await get_pool()
    user_filter = _get_user_filter(request)
    # Use q as domain search filter
    domain_filter = q.strip() if q and q.strip() else None
    jobs = await list_jobs(pool, status=status, domain=domain_filter, user_id=user_filter, limit=50)

    return get_templates().TemplateResponse(
        "pages/jobs/list.html",
        {"request": request, "active_page": "jobs", "jobs": jobs, "filter_status": status, "filter_q": q},
    )


@router.get("/jobs/new", response_class=HTMLResponse)
async def new_job_form(request: Request):
    """Redirect to dashboard — scrape form is now integrated there."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/", status_code=302)


@router.get("/jobs/bulk", response_class=HTMLResponse)
async def bulk_upload_form(request: Request):
    """Bulk CSV upload page (admin only)."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    return get_templates().TemplateResponse(
        "pages/jobs/bulk.html",
        {
            "request": request,
            "active_page": "jobs",
            "preview": None,
            "error": None,
            "enqueue_results": None,
        },
    )


@router.post("/jobs/bulk", response_class=HTMLResponse)
async def bulk_upload_preview(request: Request):
    """Parse uploaded CSV and show preview of domains to scrape."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool
    from src.services.bulk_upload import check_already_queued, parse_bulk_csv

    form = await request.form()
    csv_file = form.get("csv_file")
    max_pages = int(form.get("max_pages", 100))
    stagger_seconds = int(form.get("stagger_seconds", 30))

    # Clamp values
    max_pages = max(10, min(500, max_pages))
    stagger_seconds = max(10, min(120, stagger_seconds))

    if not csv_file or not hasattr(csv_file, "read"):
        return get_templates().TemplateResponse(
            "pages/jobs/bulk.html",
            {
                "request": request,
                "active_page": "jobs",
                "preview": None,
                "error": "Please select a CSV file.",
                "enqueue_results": None,
            },
        )

    file_content = await csv_file.read()
    result = parse_bulk_csv(file_content, filename=csv_file.filename or "")

    if result.error and not result.valid:
        return get_templates().TemplateResponse(
            "pages/jobs/bulk.html",
            {
                "request": request,
                "active_page": "jobs",
                "preview": None,
                "error": result.error,
                "enqueue_results": None,
            },
        )

    # Check for domains already queued
    pool = await get_pool()
    already_queued = await check_already_queued(
        pool, [u.domain for u in result.valid]
    )
    if already_queued:
        result.already_queued = list(already_queued)
        result.valid = [u for u in result.valid if u.domain not in already_queued]

    return get_templates().TemplateResponse(
        "pages/jobs/bulk.html",
        {
            "request": request,
            "active_page": "jobs",
            "preview": result,
            "error": result.error or None,
            "enqueue_results": None,
            "max_pages": max_pages,
            "stagger_seconds": stagger_seconds,
        },
    )


@router.post("/jobs/bulk/start")
async def bulk_upload_start(request: Request):
    """Enqueue all validated domains from the preview step."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool
    from src.services.bulk_upload import STAGGER_DELAY_SECONDS, enqueue_bulk_jobs

    form = await request.form()
    domains_str = form.get("domains", "")
    max_pages = int(form.get("max_pages", 100))
    stagger_seconds = int(form.get("stagger_seconds", STAGGER_DELAY_SECONDS))

    domains = [d.strip() for d in domains_str.split(",") if d.strip()]

    if not domains:
        return get_templates().TemplateResponse(
            "pages/jobs/bulk.html",
            {
                "request": request,
                "active_page": "jobs",
                "preview": None,
                "error": "No domains to enqueue.",
                "enqueue_results": None,
            },
        )

    pool = await get_pool()
    org_id = UUID(request.session["org_id"])
    user_id = UUID(request.session["user_id"])

    # Pass stagger_seconds explicitly rather than mutating the module-level
    # default — keeps concurrent uploads with different values isolated.
    results = await enqueue_bulk_jobs(
        pool,
        domains,
        org_id=org_id,
        user_id=user_id,
        max_pages=max_pages,
        stagger_seconds=stagger_seconds,
    )

    return get_templates().TemplateResponse(
        "pages/jobs/bulk.html",
        {
            "request": request,
            "active_page": "jobs",
            "preview": None,
            "error": None,
            "enqueue_results": results,
            "stagger_seconds": stagger_seconds,
        },
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_status_page(request: Request, job_id: UUID):
    redirect = _require_login(request)
    if redirect:
        return redirect
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

    # Non-admin can only see their own jobs
    user_filter = _get_user_filter(request)
    if user_filter and job.user_id != user_filter:
        return get_templates().TemplateResponse(
            "pages/jobs/not_found.html",
            {"request": request, "active_page": "jobs", "job_id": job_id},
            status_code=404,
        )

    data_count = await count_scraped_data_by_job(pool, job_id)

    elapsed_ms = None
    if job and job.status == "running" and job.created_at:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        created = job.created_at if job.created_at.tzinfo else job.created_at.replace(tzinfo=timezone.utc)
        elapsed_ms = int((now - created).total_seconds() * 1000)

    return get_templates().TemplateResponse(
        "pages/jobs/status.html",
        {"request": request, "active_page": "jobs", "job": job, "data_count": data_count, "elapsed_ms": elapsed_ms},
    )


@router.get("/partials/job/{job_id}/status", response_class=HTMLResponse)
async def job_status_partial(request: Request, job_id: UUID):
    redirect = _require_login(request)
    if redirect:
        return redirect
    """Job status partial for HTMX polling."""
    from src.db.pool import get_pool
    from src.db.queries.jobs import get_job
    from src.db.queries.scraped_data import count_scraped_data_by_job

    pool = await get_pool()
    job = await get_job(pool, job_id)
    data_count = await count_scraped_data_by_job(pool, job_id) if job else 0

    # Compute live elapsed time for running jobs so the template can display it
    elapsed_ms = None
    if job and job.status == "running" and job.created_at:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        created = job.created_at if job.created_at.tzinfo else job.created_at.replace(tzinfo=timezone.utc)
        elapsed_ms = int((now - created).total_seconds() * 1000)

    return get_templates().TemplateResponse(
        # Wrapper template: renders the live status panel (primary innerHTML
        # swap target) AND an out-of-band swap for #job-actions so the
        # View Results / Download CSV buttons refresh the instant the poller
        # sees the job finish — no manual reload needed.
        "partials/job_status_response.html",
        {
            "request": request,
            "job": job,
            "data_count": data_count,
            "elapsed_ms": elapsed_ms,
            # Signals to partials/job_actions.html to emit hx-swap-oob so
            # HTMX replaces the existing #job-actions element in the DOM.
            "oob": True,
        },
    )


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(request: Request, job_id: UUID):
    """Cancel a pending or running job (web UI action).

    Always returns a 302 redirect back to the job page, even if the DB update
    fails (e.g., a trigger side-effect errored). The UPDATE is idempotent and
    the cooperative-cancel check in ContentWorker will exit the worker on the
    next URL iteration.
    """
    redirect = _require_login(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool
    from src.db.queries.jobs import cancel_job as do_cancel

    pool = await get_pool()
    try:
        await do_cancel(pool, job_id)
    except Exception as e:
        logger.error("cancel_job_failed", job_id=str(job_id), error=str(e))
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=302)


@router.post("/jobs/{job_id}/retry")
async def retry_job(request: Request, job_id: UUID):
    """Retry a failed/cancelled job by creating a new job with the same params."""
    redirect = _require_login(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool
    from src.db.queries.jobs import create_job, get_job
    from src.models.job import ScrapeJobInput

    pool = await get_pool()
    original = await get_job(pool, job_id)

    if not original:
        return RedirectResponse(url="/jobs", status_code=302)

    # Create new job with same parameters
    job_input = ScrapeJobInput(domain=original.domain, template_id=original.template_id)
    org_id = original.org_id
    user_id_str = request.session.get("user_id")
    user_id = UUID(user_id_str) if user_id_str else original.user_id

    new_job = await create_job(pool, job_input, org_id=org_id, user_id=user_id)

    # Enqueue to arq worker
    try:
        from arq.connections import RedisSettings
        from arq.connections import create_pool as create_arq_pool

        from src.config.settings import get_settings

        settings = get_settings()
        redis = await create_arq_pool(RedisSettings.from_dsn(settings.redis_url))
        await redis.enqueue_job(
            "process_scrape_job",
            job_id=str(new_job.id),
            domain=original.domain,
            template_id=original.template_id or "auto",
            max_pages=100,
            data_types=["blog_url", "article", "contact", "tech_stack", "resource", "pricing"],
        )
        await redis.aclose()
    except Exception:
        pass  # Job created but enqueue failed — will stay in PENDING

    return RedirectResponse(url=f"/jobs/{new_job.id}", status_code=302)
