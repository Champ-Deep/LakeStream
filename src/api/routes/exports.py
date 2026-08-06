"""Export routes for CSV downloads and webhook integration."""

import csv
import io
import json
from datetime import UTC, datetime
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.middleware.auth import authorize_resource, get_current_user
from src.api.routes.webhook import _validate_webhook_url

# Single source of truth for the CSV shape — shared with the web download
# routes so the tech-stack columns (and everything else) never drift again.
from src.services.csv_export import CSV_FIELDNAMES as _CSV_FIELDNAMES
from src.services.csv_export import _row_for_item as _flatten_row

router = APIRouter(prefix="/export", tags=["export"])



class WebhookConfig(BaseModel):
    """Configuration for webhook export."""

    url: str
    job_id: UUID | None = None
    domain: str | None = None


async def _authorized_job_row(job_id: UUID, user: dict):
    """Fetch a scrape_jobs row and authorize the caller against it.

    Raises 404 if the job is missing or the caller can't access it.
    Returns (pool, row) for the caller to query downstream tables.
    """
    from src.db.pool import get_pool

    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT id, domain, org_id, user_id FROM scrape_jobs WHERE id = $1", job_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    authorize_resource(
        resource_org_id=row["org_id"],
        resource_user_id=row["user_id"],
        caller_org_id=user["org_id"],
        caller_user_id=user.get("user_id"),
        caller_is_admin=user.get("is_admin", False),
    )
    return pool, row


@router.get("/csv/{job_id}")
async def export_job_csv(job_id: UUID, user: dict = Depends(get_current_user)):
    """Export all scraped data from a job as CSV."""
    from src.db.queries.scraped_data import get_scraped_data_by_job

    pool, job_row = await _authorized_job_row(job_id, user)
    data = await get_scraped_data_by_job(pool, job_id)

    if not data:
        raise HTTPException(status_code=404, detail="No data found for this job")

    # Create CSV in memory with flattened metadata columns
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=_CSV_FIELDNAMES)
    writer.writeheader()
    for item in data:
        writer.writerow(_flatten_row(item))

    output.seek(0)

    domain = job_row["domain"] or "export"
    filename = f"{domain.replace('.', '_')}_{str(job_id)[:8]}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/csv")
async def export_all_csv(domain: str | None = Query(None), user: dict = Depends(get_current_user)):
    """Export scraped data as CSV, scoped to the caller's org (or user, for non-admins).

    Optionally filtered by domain.
    """
    from src.db.pool import get_pool
    from src.db.queries.scraped_data import _parse_row

    pool = await get_pool()

    # Build a single scoped query so non-admins can't read other users' data
    # and members of other orgs can't read this org's data.
    conditions: list[str] = []
    params: list[object] = []
    idx = 1

    is_admin = user.get("is_admin", False)
    if not is_admin:
        conditions.append(f"org_id = ${idx}")
        params.append(UUID(user["org_id"]))
        idx += 1
        if user.get("user_id"):
            conditions.append(f"user_id = ${idx}")
            params.append(UUID(user["user_id"]))
            idx += 1
    if domain:
        conditions.append(f"domain = ${idx}")
        params.append(domain)
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"SELECT * FROM scraped_data {where} ORDER BY scraped_at DESC LIMIT 10000"
    rows = await pool.fetch(sql, *params)
    data = [_parse_row(row) for row in rows]

    if not data:
        raise HTTPException(status_code=404, detail="No data found")

    # Create CSV in memory with flattened metadata columns
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=_CSV_FIELDNAMES)
    writer.writeheader()
    for item in data:
        writer.writerow(_flatten_row(item))

    output.seek(0)
    filename = f"{domain.replace('.', '_')}_export.csv" if domain else "lakeb2b_export.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/json/{job_id}")
async def export_job_json(job_id: UUID, user: dict = Depends(get_current_user)):
    """Export all scraped data from a job as JSON."""
    from src.db.queries.scraped_data import get_scraped_data_by_job

    pool, job_row = await _authorized_job_row(job_id, user)
    data = await get_scraped_data_by_job(pool, job_id)

    if not data:
        raise HTTPException(status_code=404, detail="No data found for this job")

    domain = job_row["domain"] or "unknown"

    # Build JSON response
    payload = {
        "job_id": str(job_id),
        "domain": domain,
        "exported_at": datetime.now(UTC).isoformat(),
        "total_records": len(data),
        "data": [
            {
                "id": str(item.id),
                "domain": item.domain,
                "data_type": item.data_type,
                "url": item.url,
                "title": item.title,
                "published_date": str(item.published_date) if item.published_date else None,
                "scraped_at": item.scraped_at.isoformat() if item.scraped_at else None,
                "metadata": item.metadata or {},
            }
            for item in data
        ],
    }

    filename = f"{domain.replace('.', '_')}_{str(job_id)[:8]}.json"

    return StreamingResponse(
        iter([json.dumps(payload, indent=2)]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/webhook")
async def export_to_webhook(config: WebhookConfig, user: dict = Depends(get_current_user)):
    """Send scraped data to a webhook URL."""
    _validate_webhook_url(config.url)
    from src.db.pool import get_pool
    from src.db.queries.scraped_data import _parse_row, get_scraped_data_by_job

    if config.job_id:
        pool, _job_row = await _authorized_job_row(config.job_id, user)
        data = await get_scraped_data_by_job(pool, config.job_id)
    elif config.domain:
        pool = await get_pool()
        # Scope by org/user so a caller can't exfiltrate another org's data
        # by guessing a domain another tenant has scraped.
        conditions = ["domain = $1"]
        params: list[object] = [config.domain]
        idx = 2
        if not user.get("is_admin", False):
            conditions.append(f"org_id = ${idx}")
            params.append(UUID(user["org_id"]))
            idx += 1
            if user.get("user_id"):
                conditions.append(f"user_id = ${idx}")
                params.append(UUID(user["user_id"]))
                idx += 1
        sql = (
            f"SELECT * FROM scraped_data WHERE {' AND '.join(conditions)} "
            "ORDER BY scraped_at DESC LIMIT 1000"
        )
        rows = await pool.fetch(sql, *params)
        data = [_parse_row(row) for row in rows]
    else:
        raise HTTPException(status_code=400, detail="Either job_id or domain is required")

    if not data:
        raise HTTPException(status_code=404, detail="No data found")

    # Prepare payload
    payload = {
        "source": "lake_b2b_scraper",
        "count": len(data),
        "data": [
            {
                "id": str(item.id),
                "domain": item.domain,
                "data_type": item.data_type,
                "url": item.url,
                "title": item.title,
                "published_date": str(item.published_date) if item.published_date else None,
                "metadata": item.metadata,
                "scraped_at": item.scraped_at.isoformat() if item.scraped_at else None,
            }
            for item in data
        ],
    }

    # Send to webhook
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                config.url,
                json=payload,
                headers={"Content-Type": "application/json", "User-Agent": "Lake-B2B-Scraper/1.0"},
            )
            response.raise_for_status()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Webhook request timed out")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Webhook request failed: {e!s}")

    return {
        "success": True,
        "records_sent": len(data),
        "webhook_url": config.url,
        "webhook_status": response.status_code,
    }
