"""Results browsing, domain analytics, and CSV downloads."""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.api.routes.web._shared import _get_user_filter, _require_login, get_templates

router = APIRouter(tags=["web"])


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
    from src.db.queries.scraped_data import (
        count_by_data_type,
        count_search_scraped_data,
        list_distinct_domains,
        search_scraped_data,
    )

    pool = await get_pool()
    limit = 50
    offset = (page - 1) * limit
    user_filter = _get_user_filter(request)

    # Get unique domains for filter dropdown (scoped to user)
    domains = await list_distinct_domains(pool, user_id=user_filter)

    # Get per-type counts for the dropdown (scoped to user)
    type_count_rows = await count_by_data_type(pool, user_id=user_filter)
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

    search_kwargs = {
        "user_id": user_filter,
        "domain": domain,
        "data_type": data_type,
        "q": q,
        "hide_pages_by_default": True,
    }
    results = await search_scraped_data(pool, limit=limit, offset=offset, **search_kwargs)
    total = await count_search_scraped_data(pool, **search_kwargs)

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
    from src.db.queries.scraped_data import get_data_type_breakdown_for_domain

    pool = await get_pool()
    user_filter = _get_user_filter(request)
    domain_meta = await get_domain_metadata(pool, domain)
    jobs = await list_jobs(pool, domain=domain, user_id=user_filter, limit=10)

    # Get data type breakdown (scoped to user)
    breakdown = await get_data_type_breakdown_for_domain(pool, domain, user_id=user_filter)

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
# DOWNLOADS (CSV export from browser session)
# =============================================================================


@router.get("/download/job/{job_id}")
async def download_job_csv(request: Request, job_id: UUID):
    """Download scraped data for a job as CSV (session-based auth)."""
    redirect = _require_login(request)
    if redirect:
        return redirect

    from fastapi.responses import StreamingResponse

    from src.db.pool import get_pool
    from src.db.queries.jobs import get_job
    from src.db.queries.scraped_data import get_scraped_data_by_job
    from src.services.csv_export import scraped_data_to_csv

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

    csv_content = scraped_data_to_csv(data)
    domain = job.domain.replace(".", "_")
    filename = f"{domain}_{str(job_id)[:8]}.csv"

    return StreamingResponse(
        iter([csv_content]),
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

    from fastapi.responses import StreamingResponse

    from src.db.pool import get_pool
    from src.db.queries.scraped_data import search_scraped_data
    from src.services.csv_export import scraped_data_to_csv

    pool = await get_pool()
    user_filter = _get_user_filter(request)

    data = await search_scraped_data(
        pool,
        user_id=user_filter,
        domain=domain,
        data_type=data_type,
        q=q,
        limit=10000,
    )

    if not data:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="No data found")

    csv_content = scraped_data_to_csv(data)
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="lakestream_export.csv"'},
    )
