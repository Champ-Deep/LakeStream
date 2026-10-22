"""Result browsing, domain views and CSV downloads."""

from uuid import UUID

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

    # Recent content changes (v2 change monitoring), scoped to the user
    from src.db.queries.content_changes import get_recent_changes_by_domain

    recent_changes = [
        dict(row)
        for row in await get_recent_changes_by_domain(
            pool, domain, user_id=user_filter, limit=20
        )
    ]

    return get_templates().TemplateResponse(
        "pages/domains/detail.html",
        {
            "request": request,
            "active_page": "domains",
            "domain": domain,
            "metadata": domain_meta,
            "jobs": jobs,
            "breakdown": breakdown,
            "recent_changes": recent_changes,
        },
    )


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
