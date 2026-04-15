"""Web UI routes for the LakeStream dashboard."""

from uuid import UUID

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

logger = structlog.get_logger()

router = APIRouter(tags=["web"])


def get_templates():
    """Get templates instance from app state."""
    from src.server import templates

    return templates


def _require_login(request: Request):
    """Return a redirect to /login if the user is not in session, else None."""
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return None


def _require_admin(request: Request):
    """Return a redirect if user is not admin, else None."""
    redir = _require_login(request)
    if redir:
        return redir
    if not request.session.get("is_admin"):
        return RedirectResponse(url="/", status_code=302)
    return None


def _get_user_filter(request: Request) -> UUID | None:
    """Return user_id for filtering data, or None if admin (sees all)."""
    if request.session.get("is_admin"):
        return None  # Admin sees everything
    uid = request.session.get("user_id")
    return UUID(uid) if uid else None


# =============================================================================
# AUTH PAGES
# =============================================================================


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=302)
    return get_templates().TemplateResponse(
        "pages/login.html", {"request": request, "error": None, "email": None}
    )


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    """Handle login form submission."""
    from src.db.pool import get_pool
    from src.db.queries.users import get_user_by_email
    from src.services.auth import verify_password

    pool = await get_pool()
    user = await get_user_by_email(pool, email)

    if not user or not verify_password(password, user.password_hash):
        return get_templates().TemplateResponse(
            "pages/login.html",
            {"request": request, "error": "Invalid email or password", "email": email},
            status_code=401,
        )

    if not user.is_active:
        return get_templates().TemplateResponse(
            "pages/login.html",
            {"request": request, "error": "Account is disabled", "email": email},
            status_code=403,
        )

    request.session["user_id"] = str(user.id)
    request.session["org_id"] = str(user.org_id)
    request.session["role"] = user.role
    request.session["email"] = user.email
    request.session["is_admin"] = user.is_admin
    request.session["full_name"] = user.full_name or user.email

    from src.config.settings import get_settings
    from src.services.auth import create_access_token

    cfg = get_settings()
    token = create_access_token(user.id, user.org_id, user.role, is_admin=user.is_admin)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        "access_token",
        token,
        httponly=False,  # must be JS-readable so getAuthHeaders() can build Bearer header
        samesite="lax",
        max_age=cfg.access_token_expire_hours * 3600,
    )
    return response


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    """Signup page."""
    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=302)
    return get_templates().TemplateResponse(
        "pages/signup.html",
        {"request": request, "error": None, "email": None, "full_name": None, "org_name": None},
    )


@router.post("/signup")
async def signup_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    org_name: str = Form(...),
):
    """Handle signup form submission."""
    from src.db.pool import get_pool
    from src.db.queries.users import create_organization, create_user, get_user_by_email
    from src.services.auth import hash_password

    pool = await get_pool()

    # Check if email already exists
    existing = await get_user_by_email(pool, email)
    if existing:
        return get_templates().TemplateResponse(
            "pages/signup.html",
            {
                "request": request,
                "error": "Email already registered",
                "email": email,
                "full_name": full_name,
                "org_name": org_name,
            },
            status_code=400,
        )

    if len(password) < 8:
        return get_templates().TemplateResponse(
            "pages/signup.html",
            {
                "request": request,
                "error": "Password must be at least 8 characters",
                "email": email,
                "full_name": full_name,
                "org_name": org_name,
            },
            status_code=400,
        )

    # Create org + user
    org = await create_organization(pool, org_name)
    password_hash = hash_password(password)
    user = await create_user(
        pool,
        email=email,
        password_hash=password_hash,
        full_name=full_name,
        org_id=org.id,
        role="org_owner",
    )

    # Log them in
    request.session["user_id"] = str(user.id)
    request.session["org_id"] = str(user.org_id)
    request.session["role"] = user.role
    request.session["email"] = user.email
    request.session["is_admin"] = user.is_admin
    request.session["full_name"] = user.full_name or user.email

    from src.config.settings import get_settings
    from src.services.auth import create_access_token

    cfg = get_settings()
    token = create_access_token(user.id, user.org_id, user.role, is_admin=user.is_admin)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        "access_token",
        token,
        httponly=False,
        samesite="lax",
        max_age=cfg.access_token_expire_hours * 3600,
    )
    return response


@router.get("/logout")
async def logout(request: Request):
    """Log out and clear session + JWT cookie."""
    request.session.clear()
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("access_token", path="/")
    return response


# =============================================================================
# DASHBOARD
# =============================================================================


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


# =============================================================================
# PARTIALS (HTMX fragments)
# =============================================================================


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

    # Override stagger delay if user chose a different value
    import src.services.bulk_upload as bulk_mod
    original_stagger = bulk_mod.STAGGER_DELAY_SECONDS
    bulk_mod.STAGGER_DELAY_SECONDS = stagger_seconds

    try:
        results = await enqueue_bulk_jobs(
            pool,
            domains,
            org_id=org_id,
            user_id=user_id,
            max_pages=max_pages,
        )
    finally:
        bulk_mod.STAGGER_DELAY_SECONDS = original_stagger

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
        "partials/job_status.html",
        {"request": request, "job": job, "data_count": data_count, "elapsed_ms": elapsed_ms},
    )


@router.get("/partials/job/{job_id}/graph", response_class=HTMLResponse)
async def job_graph_partial(request: Request, job_id: UUID):
    """Sitemap graph partial for the Graph tab on the job status page.

    Builds a GraphData payload (nodes + edges) from the URLs scraped for
    this job. Phase 1: hierarchy inferred from URL paths, no new backend
    endpoints, no new DB tables. Phase 2/3 can inject additional fields
    into the same payload without changing this route's contract.
    """
    redirect = _require_login(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool
    from src.db.queries.jobs import get_job
    from src.db.queries.scraped_data import get_scraped_data_by_job
    from src.services.sitemap_graph import build_sitemap_graph

    pool = await get_pool()
    job = await get_job(pool, job_id)
    if not job:
        return get_templates().TemplateResponse(
            "pages/jobs/not_found.html",
            {"request": request, "active_page": "jobs", "job_id": job_id},
            status_code=404,
        )

    # Non-admin can only see their own jobs.
    user_filter = _get_user_filter(request)
    if user_filter and job.user_id != user_filter:
        return get_templates().TemplateResponse(
            "pages/jobs/not_found.html",
            {"request": request, "active_page": "jobs", "job_id": job_id},
            status_code=404,
        )

    records = await get_scraped_data_by_job(pool, job_id)
    # Dedupe URLs at the source; build_sitemap_graph also dedupes but
    # doing it here keeps total_urls honest.
    urls = sorted({r.url for r in records if r.url})

    # Performance floor: if the graph would exceed 500 nodes, drop
    # everything beyond depth 3 and warn the user.
    PERFORMANCE_NODE_LIMIT = 500
    MAX_DEPTH_WHEN_COLLAPSED = 3

    graph_data = build_sitemap_graph(urls)
    collapsed = False
    if len(graph_data["nodes"]) > PERFORMANCE_NODE_LIMIT:
        collapsed = True
        keep_ids = {
            n["id"] for n in graph_data["nodes"]
            if n["depth"] <= MAX_DEPTH_WHEN_COLLAPSED
        }
        graph_data = {
            "nodes": [n for n in graph_data["nodes"] if n["id"] in keep_ids],
            "edges": [
                e for e in graph_data["edges"]
                if e["from"] in keep_ids and e["to"] in keep_ids
            ],
        }

    return get_templates().TemplateResponse(
        "partials/job_graph.html",
        {
            "request": request,
            "job": job,
            "graph_data": graph_data,
            "total_urls": len(urls),
            "collapsed": collapsed,
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


# =============================================================================
# RESULTS PAGES
# =============================================================================


@router.get("/results", response_class=HTMLResponse)
async def results_browse(
    request: Request,
    domain: str | None = None,
    data_type: str | None = None,
    q: str | None = None,
    page: int = 1,
):
    redirect = _require_login(request)
    if redirect:
        return redirect
    """Results browser page."""
    from src.db.pool import get_pool
    from src.db.queries.scraped_data import _parse_row

    pool = await get_pool()
    limit = 50
    offset = (page - 1) * limit
    user_filter = _get_user_filter(request)

    # Normalize an incoming `domain` param. Job records may store either a bare
    # hostname ("example.com") or, for some user-pasted inputs, a full URL with
    # path + query. scraped_data.domain, however, is always the hostname. Strip
    # protocol, path and "www." prefix so the filter works for either form.
    if domain:
        from urllib.parse import urlparse

        parsed = urlparse(domain if "://" in domain else f"http://{domain}")
        host = (parsed.hostname or domain).lower()
        if host.startswith("www."):
            host = host[4:]
        domain = host

    # Get unique domains for filter dropdown (scoped to user)
    if user_filter:
        domains_rows = await pool.fetch(
            "SELECT DISTINCT domain FROM scraped_data WHERE user_id = $1 ORDER BY domain",
            user_filter,
        )
    else:
        domains_rows = await pool.fetch("SELECT DISTINCT domain FROM scraped_data ORDER BY domain")
    domains = [row["domain"] for row in domains_rows]

    # Get per-type counts for the dropdown (scoped to user)
    if user_filter:
        type_count_rows = await pool.fetch(
            "SELECT data_type, COUNT(*) AS cnt FROM scraped_data "
            "WHERE user_id = $1 GROUP BY data_type ORDER BY cnt DESC",
            user_filter,
        )
    else:
        type_count_rows = await pool.fetch(
            "SELECT data_type, COUNT(*) AS cnt FROM scraped_data "
            "GROUP BY data_type ORDER BY cnt DESC"
        )
    # List of {value, label, count} for the template dropdown
    _TYPE_LABELS = {
        "blog_url": "Blog URLs",
        "article": "Articles",
        "contact": "Contacts",
        "tech_stack": "Tech Stack",
        "resource": "Resources",
        "pricing": "Pricing",
        "page": "Pages",
        "document": "Documents",
        "extracted": "Extracted",
    }
    data_types_list = []
    for row in type_count_rows:
        dt = row["data_type"]
        data_types_list.append({
            "value": dt,
            "label": _TYPE_LABELS.get(dt, dt.replace("_", " ").title()),
            "count": row["cnt"],
        })

    # Build dynamic query for results
    conditions = []
    vals: list = []
    idx = 1

    if user_filter:
        conditions.append(f"user_id = ${idx}")
        vals.append(user_filter)
        idx += 1
    if domain:
        # ILIKE %host% so both "linkedin.com" and "www.linkedin.com" rows match,
        # mirroring the pattern used by list_jobs (src/db/queries/jobs.py).
        conditions.append(f"domain ILIKE ${idx}")
        vals.append(f"%{domain}%")
        idx += 1
    if data_type:
        conditions.append(f"data_type = ${idx}")
        vals.append(data_type)
        idx += 1
    elif not q:
        # Default view: hide raw page records (they clutter useful results).
        # Users can still see them by explicitly selecting "Pages" from the dropdown.
        conditions.append("data_type != 'page'")
    if q and q.strip():
        # Deep search: search title, URL, domain AND inside metadata JSONB content.
        # metadata::text casts the entire JSON to a text string for ILIKE matching,
        # so keywords in article body, contact names, descriptions etc. are found.
        search_cond = (
            f"(title ILIKE ${idx} OR url ILIKE ${idx} "
            f"OR domain ILIKE ${idx} OR metadata::text ILIKE ${idx})"
        )
        conditions.append(search_cond)
        vals.append(f"%{q.strip()}%")
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # When keyword is active, rank results by relevance:
    # title match first, then URL match, then content match
    if q and q.strip():
        q_idx = idx  # next param slot
        vals_for_query = vals.copy()
        vals_for_query.append(f"%{q.strip()}%")  # for ORDER BY ranking
        vals_for_query.extend([limit, offset])
        order_clause = (
            f"ORDER BY "
            f"CASE WHEN title ILIKE ${q_idx} THEN 0 "
            f"WHEN url ILIKE ${q_idx} THEN 1 "
            f"WHEN domain ILIKE ${q_idx} THEN 2 "
            f"ELSE 3 END, "
            f"scraped_at DESC"
        )
        query = (
            f"SELECT * FROM scraped_data {where} "
            f"{order_clause} LIMIT ${q_idx + 1} OFFSET ${q_idx + 2}"
        )
        rows = await pool.fetch(query, *vals_for_query)
    else:
        vals.extend([limit, offset])
        query = (
            f"SELECT * FROM scraped_data {where} "
            f"ORDER BY scraped_at DESC LIMIT ${idx} OFFSET ${idx + 1}"
        )
        rows = await pool.fetch(query, *vals)

    results = [_parse_row(row) for row in rows]

    # Get total count with same filters (exclude limit/offset params)
    count_vals = vals[:len(vals) - 2] if not (q and q.strip()) else vals.copy()
    count_query = f"SELECT COUNT(*) FROM scraped_data {where}"
    if count_vals:
        total = await pool.fetchval(count_query, *count_vals)
    else:
        total = await pool.fetchval(count_query)

    # Build download URL with current filters
    download_params = []
    if domain:
        download_params.append(f"domain={domain}")
    if data_type:
        download_params.append(f"data_type={data_type}")
    if q:
        download_params.append(f"q={q}")
    download_qs = f"?{'&'.join(download_params)}" if download_params else ""

    return get_templates().TemplateResponse(
        "pages/results/browse.html",
        {
            "request": request,
            "active_page": "results",
            "results": results,
            "domains": domains,
            "data_types_list": data_types_list,
            "filter_domain": domain,
            "filter_data_type": data_type,
            "filter_q": q,
            "page": page,
            "total": total,
            "limit": limit,
            "download_qs": download_qs,
        },
    )


# =============================================================================
# DOMAINS PAGES
# =============================================================================


@router.get("/domains", response_class=HTMLResponse)
async def domains_list(request: Request, sort_by: str = "last_scraped_at"):
    redirect = _require_login(request)
    if redirect:
        return redirect
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
    redirect = _require_login(request)
    if redirect:
        return redirect
    """Domain detail page."""
    from src.db.pool import get_pool
    from src.db.queries.domains import get_domain_metadata
    from src.db.queries.jobs import list_jobs

    pool = await get_pool()
    user_filter = _get_user_filter(request)
    domain_meta = await get_domain_metadata(pool, domain)
    jobs = await list_jobs(pool, domain=domain, user_id=user_filter, limit=10)

    # Get data type breakdown (scoped to user)
    if user_filter:
        breakdown_rows = await pool.fetch(
            """
            SELECT data_type, COUNT(*) as count
            FROM scraped_data
            WHERE domain = $1 AND user_id = $2
            GROUP BY data_type
            """,
            domain,
            user_filter,
        )
    else:
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
    redirect = _require_login(request)
    if redirect:
        return redirect
    """Help and documentation page."""
    return get_templates().TemplateResponse(
        "pages/help/index.html", {"request": request, "active_page": "help"}
    )


# =============================================================================
# SETTINGS PAGES
# =============================================================================


@router.get("/settings/api-docs", response_class=HTMLResponse)
async def api_docs_page(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect
    """Full API documentation page."""
    return get_templates().TemplateResponse(
        "pages/settings/api_docs.html",
        {"request": request, "active_page": "settings"},
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect
    """Settings and webhook configuration page."""
    from src.config.settings import get_settings
    from src.db.pool import get_pool

    settings = get_settings()
    webhook_trigger_url = f"{settings.base_url}/api/webhook/trigger"

    # Load org proxy settings for initial render
    proxy_url = ""
    try:
        pool = await get_pool()
        org_id = getattr(request.state, "org_id", None)
        if not org_id:
            org_id = await pool.fetchval("SELECT id FROM organizations WHERE slug = 'default'")
        if org_id:
            proxy_url = (
                await pool.fetchval("SELECT proxy_url FROM organizations WHERE id = $1", org_id)
                or ""
            )
    except Exception:
        pass  # Settings page works without proxy info

    return get_templates().TemplateResponse(
        "pages/settings/index.html",
        {
            "request": request,
            "active_page": "settings",
            "webhook_trigger_url": webhook_trigger_url,
            "proxy_url": proxy_url,
        },
    )


# =============================================================================
# DOWNLOADS (CSV export from browser session)
# =============================================================================


@router.get("/download/job/{job_id}")
async def download_job_csv(request: Request, job_id: UUID):
    """Download scraped data for a job as CSV (session-based auth)."""
    redirect = _require_login(request)
    if redirect:
        return redirect

    import csv
    import io

    from fastapi.responses import StreamingResponse

    from src.db.pool import get_pool
    from src.db.queries.jobs import get_job
    from src.db.queries.scraped_data import get_scraped_data_by_job

    pool = await get_pool()
    job = await get_job(pool, job_id)
    if not job:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Job not found")

    # Non-admin can only download their own jobs
    user_filter = _get_user_filter(request)
    if user_filter and job.user_id != user_filter:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Access denied")

    data = await get_scraped_data_by_job(pool, job_id)
    if not data:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="No data found for this job")

    # Flatten to CSV
    fieldnames = [
        "domain",
        "data_type",
        "url",
        "title",
        "published_date",
        "scraped_at",
        "author",
        "excerpt",
        "word_count",
        "categories",
        "content",
        "first_name",
        "last_name",
        "job_title",
        "email",
        "phone",
        "linkedin_url",
        "total_articles",
        "platform",
        "frameworks",
        "js_libraries",
        "analytics",
        "resource_type",
        "description",
        "download_url",
        "plan_name",
        "price",
        "billing_cycle",
        "features",
        "has_free_trial",
        "cta_text",
    ]

    def _join_list(meta, key):
        val = meta.get(key, [])
        return "; ".join(val) if isinstance(val, list) else ""

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for item in data:
        meta = item.metadata or {}
        writer.writerow(
            {
                "domain": item.domain,
                "data_type": item.data_type,
                "url": item.url or "",
                "title": item.title or "",
                "published_date": str(item.published_date) if item.published_date else "",
                "scraped_at": item.scraped_at.isoformat() if item.scraped_at else "",
                "author": meta.get("author", ""),
                "excerpt": meta.get("excerpt", ""),
                "word_count": meta.get("word_count", ""),
                "categories": _join_list(meta, "categories"),
                "content": meta.get("content", ""),
                "first_name": meta.get("first_name", ""),
                "last_name": meta.get("last_name", ""),
                "job_title": meta.get("job_title", ""),
                "email": meta.get("email", ""),
                "phone": meta.get("phone", ""),
                "linkedin_url": meta.get("linkedin_url", ""),
                "total_articles": meta.get("total_articles", ""),
                "platform": meta.get("platform", ""),
                "frameworks": _join_list(meta, "frameworks"),
                "js_libraries": _join_list(meta, "js_libraries"),
                "analytics": _join_list(meta, "analytics"),
                "resource_type": meta.get("resource_type", ""),
                "description": meta.get("description", ""),
                "download_url": meta.get("download_url", ""),
                "plan_name": meta.get("plan_name", ""),
                "price": meta.get("price", ""),
                "billing_cycle": meta.get("billing_cycle", ""),
                "features": _join_list(meta, "features"),
                "has_free_trial": meta.get("has_free_trial", ""),
                "cta_text": meta.get("cta_text", ""),
            }
        )

    output.seek(0)
    domain = job.domain.replace(".", "_")
    filename = f"{domain}_{str(job_id)[:8]}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/download/all")
async def download_all_csv(
    request: Request,
    domain: str | None = None,
    data_type: str | None = None,
    q: str | None = None,
):
    """Download scraped data as CSV. Respects the same filters as /results."""
    redirect = _require_login(request)
    if redirect:
        return redirect

    import csv
    import io

    from fastapi.responses import StreamingResponse

    from src.db.pool import get_pool
    from src.db.queries.scraped_data import _parse_row

    pool = await get_pool()
    user_filter = _get_user_filter(request)

    # Normalize domain to hostname (same logic as /results) so CSV export from
    # a filtered results page matches what the page shows.
    if domain:
        from urllib.parse import urlparse

        parsed = urlparse(domain if "://" in domain else f"http://{domain}")
        host = (parsed.hostname or domain).lower()
        if host.startswith("www."):
            host = host[4:]
        domain = host

    # Build the same filter conditions as the /results route
    conditions = []
    vals: list = []
    idx = 1

    if user_filter:
        conditions.append(f"user_id = ${idx}")
        vals.append(user_filter)
        idx += 1
    if domain:
        # ILIKE %host% so both "linkedin.com" and "www.linkedin.com" rows match,
        # mirroring the pattern used by list_jobs (src/db/queries/jobs.py).
        conditions.append(f"domain ILIKE ${idx}")
        vals.append(f"%{domain}%")
        idx += 1
    if data_type:
        conditions.append(f"data_type = ${idx}")
        vals.append(data_type)
        idx += 1
    if q and q.strip():
        conditions.append(
            f"(title ILIKE ${idx} OR url ILIKE ${idx} "
            f"OR domain ILIKE ${idx} OR metadata::text ILIKE ${idx})"
        )
        vals.append(f"%{q.strip()}%")
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    download_query = f"SELECT * FROM scraped_data {where} ORDER BY scraped_at DESC LIMIT 10000"
    if vals:
        rows = await pool.fetch(download_query, *vals)
    else:
        rows = await pool.fetch(download_query)

    data = [_parse_row(row) for row in rows]

    if not data:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="No data found")

    fieldnames = [
        "domain",
        "data_type",
        "url",
        "title",
        "published_date",
        "scraped_at",
        "author",
        "excerpt",
        "word_count",
        "categories",
        "content",
        "first_name",
        "last_name",
        "job_title",
        "email",
        "phone",
        "linkedin_url",
        "total_articles",
        "platform",
        "frameworks",
        "js_libraries",
        "analytics",
        "resource_type",
        "description",
        "download_url",
        "plan_name",
        "price",
        "billing_cycle",
        "features",
        "has_free_trial",
        "cta_text",
    ]

    def _join_list(meta, key):
        val = meta.get(key, [])
        return "; ".join(val) if isinstance(val, list) else ""

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for item in data:
        meta = item.metadata or {}
        writer.writerow(
            {
                "domain": item.domain,
                "data_type": item.data_type,
                "url": item.url or "",
                "title": item.title or "",
                "published_date": str(item.published_date) if item.published_date else "",
                "scraped_at": item.scraped_at.isoformat() if item.scraped_at else "",
                "author": meta.get("author", ""),
                "excerpt": meta.get("excerpt", ""),
                "word_count": meta.get("word_count", ""),
                "categories": _join_list(meta, "categories"),
                "content": meta.get("content", ""),
                "first_name": meta.get("first_name", ""),
                "last_name": meta.get("last_name", ""),
                "job_title": meta.get("job_title", ""),
                "email": meta.get("email", ""),
                "phone": meta.get("phone", ""),
                "linkedin_url": meta.get("linkedin_url", ""),
                "total_articles": meta.get("total_articles", ""),
                "platform": meta.get("platform", ""),
                "frameworks": _join_list(meta, "frameworks"),
                "js_libraries": _join_list(meta, "js_libraries"),
                "analytics": _join_list(meta, "analytics"),
                "resource_type": meta.get("resource_type", ""),
                "description": meta.get("description", ""),
                "download_url": meta.get("download_url", ""),
                "plan_name": meta.get("plan_name", ""),
                "price": meta.get("price", ""),
                "billing_cycle": meta.get("billing_cycle", ""),
                "features": _join_list(meta, "features"),
                "has_free_trial": meta.get("has_free_trial", ""),
                "cta_text": meta.get("cta_text", ""),
            }
        )

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="lakestream_export.csv"'},
    )


# =============================================================================
# USER MANAGEMENT (Admin only)
# =============================================================================


@router.get("/users", response_class=HTMLResponse)
async def users_list(request: Request):
    """User management page (admin only)."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool

    pool = await get_pool()

    # Get all users with their job counts
    rows = await pool.fetch(
        """
        SELECT u.*,
               o.name as org_name,
               COALESCE(j.job_count, 0) as job_count,
               COALESCE(d.data_count, 0) as data_count
        FROM users u
        JOIN organizations o ON u.org_id = o.id
        LEFT JOIN (
            SELECT user_id, COUNT(*) as job_count FROM scrape_jobs GROUP BY user_id
        ) j ON j.user_id = u.id
        LEFT JOIN (
            SELECT user_id, COUNT(*) as data_count FROM scraped_data GROUP BY user_id
        ) d ON d.user_id = u.id
        ORDER BY u.created_at DESC
        """
    )

    users = []
    for row in rows:
        users.append(
            {
                "id": row["id"],
                "email": row["email"],
                "full_name": row["full_name"],
                "role": row["role"],
                "is_admin": row["is_admin"],
                "is_active": row["is_active"],
                "org_name": row["org_name"],
                "job_count": row["job_count"],
                "data_count": row["data_count"],
                "last_login_at": row["last_login_at"],
                "created_at": row["created_at"],
            }
        )

    return get_templates().TemplateResponse(
        "pages/users/list.html",
        {
            "request": request,
            "active_page": "users",
            "users": users,
        },
    )


@router.post("/users/create")
async def create_user_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    role: str = Form(default="member"),
    is_admin: bool = Form(default=False),
):
    """Create a new user (admin only)."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool
    from src.db.queries.users import create_user, get_user_by_email
    from src.services.auth import hash_password

    pool = await get_pool()

    # Check if email already exists
    existing = await get_user_by_email(pool, email)
    if existing:
        # Re-render with error
        return RedirectResponse(url="/users?error=email_exists", status_code=302)

    # Use the admin's org_id
    org_id = UUID(request.session["org_id"])

    password_hash = hash_password(password)
    await create_user(
        pool,
        email=email,
        password_hash=password_hash,
        full_name=full_name,
        org_id=org_id,
        role=role,
    )

    # Set is_admin flag if needed
    if is_admin:
        user = await get_user_by_email(pool, email)
        if user:
            await pool.execute("UPDATE users SET is_admin = TRUE WHERE id = $1", user.id)

    return RedirectResponse(url="/users?success=created", status_code=302)


@router.post("/users/{user_id}/toggle-active")
async def toggle_user_active(request: Request, user_id: UUID):
    """Enable/disable a user (admin only)."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool

    pool = await get_pool()

    # Don't allow disabling yourself
    if str(user_id) == request.session.get("user_id"):
        return RedirectResponse(url="/users?error=cannot_disable_self", status_code=302)

    await pool.execute(
        "UPDATE users SET is_active = NOT is_active, updated_at = NOW() WHERE id = $1",
        user_id,
    )
    return RedirectResponse(url="/users", status_code=302)


@router.post("/users/{user_id}/toggle-admin")
async def toggle_user_admin(request: Request, user_id: UUID):
    """Toggle admin status for a user (admin only)."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool

    pool = await get_pool()

    # Don't allow removing your own admin
    if str(user_id) == request.session.get("user_id"):
        return RedirectResponse(url="/users?error=cannot_change_self", status_code=302)

    await pool.execute(
        "UPDATE users SET is_admin = NOT is_admin, updated_at = NOW() WHERE id = $1",
        user_id,
    )
    return RedirectResponse(url="/users", status_code=302)


@router.post("/users/{user_id}/delete")
async def delete_user(request: Request, user_id: UUID):
    """Delete a user (admin only)."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool

    pool = await get_pool()

    # Don't allow deleting yourself
    if str(user_id) == request.session.get("user_id"):
        return RedirectResponse(url="/users?error=cannot_delete_self", status_code=302)

    await pool.execute("DELETE FROM users WHERE id = $1", user_id)
    return RedirectResponse(url="/users", status_code=302)


@router.post("/users/{user_id}/edit")
async def edit_user(
    request: Request,
    user_id: UUID,
    full_name: str = Form(...),
    email: str = Form(...),
):
    """Edit a user's name and email (admin only)."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    from asyncpg import UniqueViolationError

    from src.db.pool import get_pool
    from src.db.queries.users import update_user

    pool = await get_pool()
    try:
        await update_user(pool, user_id, full_name.strip(), email.strip().lower())
    except UniqueViolationError:
        return RedirectResponse(url="/users?error=email_exists", status_code=302)
    return RedirectResponse(url="/users?success=updated", status_code=302)


@router.get("/account", response_class=HTMLResponse)
async def account_page(request: Request):
    """Self-service account settings page."""
    redirect = _require_login(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool
    from src.db.queries.users import get_user_by_id

    pool = await get_pool()
    user_id = UUID(request.session["user_id"])
    user = await get_user_by_id(pool, user_id)

    return get_templates().TemplateResponse(
        "pages/account/index.html",
        {
            "request": request,
            "active_page": "account",
            "user": user,
        },
    )


@router.post("/account/update")
async def account_update(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
):
    """Update the logged-in user's name and email."""
    redirect = _require_login(request)
    if redirect:
        return redirect

    from asyncpg import UniqueViolationError

    from src.db.pool import get_pool
    from src.db.queries.users import update_user

    pool = await get_pool()
    user_id = UUID(request.session["user_id"])
    try:
        updated = await update_user(pool, user_id, full_name.strip(), email.strip().lower())
        if updated:
            # Keep session email in sync
            request.session["email"] = updated.email
    except UniqueViolationError:
        return RedirectResponse(url="/account?error=email_exists", status_code=302)
    return RedirectResponse(url="/account?success=updated", status_code=302)
