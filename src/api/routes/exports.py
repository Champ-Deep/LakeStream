"""Export routes for CSV downloads and webhook integration."""

import csv
import io
import json
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(prefix="/export", tags=["export"])


class WebhookConfig(BaseModel):
    """Configuration for webhook export."""

    url: str
    job_id: UUID | None = None
    domain: str | None = None


@router.get("/csv/{job_id}")
async def export_job_csv(job_id: UUID):
    """Export all scraped data from a job as CSV."""
    from src.db.pool import get_pool
    from src.db.queries.scraped_data import get_scraped_data_by_job

    pool = await get_pool()
    data = await get_scraped_data_by_job(pool, job_id)

    if not data:
        raise HTTPException(status_code=404, detail="No data found for this job")

    # Create CSV in memory
    output = io.StringIO()
    fieldnames = ["domain", "data_type", "url", "title", "published_date", "metadata", "scraped_at"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for item in data:
        writer.writerow(
            {
                "domain": item.domain,
                "data_type": item.data_type,
                "url": item.url or "",
                "title": item.title or "",
                "published_date": str(item.published_date) if item.published_date else "",
                "metadata": json.dumps(item.metadata),
                "scraped_at": item.scraped_at.isoformat() if item.scraped_at else "",
            }
        )

    output.seek(0)

    # Get domain for filename
    job_row = await pool.fetchrow("SELECT domain FROM scrape_jobs WHERE id = $1", job_id)
    domain = job_row["domain"] if job_row else "export"
    filename = f"{domain.replace('.', '_')}_{str(job_id)[:8]}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/csv")
async def export_all_csv(domain: str | None = Query(None)):
    """Export all scraped data as CSV, optionally filtered by domain."""
    from src.db.pool import get_pool
    from src.db.queries.scraped_data import _parse_row, get_scraped_data_by_domain

    pool = await get_pool()

    if domain:
        data = await get_scraped_data_by_domain(pool, domain, limit=10000)
    else:
        rows = await pool.fetch("SELECT * FROM scraped_data ORDER BY scraped_at DESC LIMIT 10000")
        data = [_parse_row(row) for row in rows]

    if not data:
        raise HTTPException(status_code=404, detail="No data found")

    # Create CSV in memory
    output = io.StringIO()
    fieldnames = ["domain", "data_type", "url", "title", "published_date", "metadata", "scraped_at"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for item in data:
        writer.writerow(
            {
                "domain": item.domain,
                "data_type": item.data_type,
                "url": item.url or "",
                "title": item.title or "",
                "published_date": str(item.published_date) if item.published_date else "",
                "metadata": json.dumps(item.metadata),
                "scraped_at": item.scraped_at.isoformat() if item.scraped_at else "",
            }
        )

    output.seek(0)
    filename = f"{domain.replace('.', '_')}_export.csv" if domain else "lakeb2b_export.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/webhook")
async def export_to_webhook(config: WebhookConfig):
    """Send scraped data to a webhook URL."""
    from src.db.pool import get_pool
    from src.db.queries.scraped_data import get_scraped_data_by_domain, get_scraped_data_by_job

    pool = await get_pool()

    # Get data based on config
    if config.job_id:
        data = await get_scraped_data_by_job(pool, config.job_id)
    elif config.domain:
        data = await get_scraped_data_by_domain(pool, config.domain, limit=1000)
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
