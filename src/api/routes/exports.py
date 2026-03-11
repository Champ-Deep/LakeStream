"""Export routes for CSV downloads and webhook integration."""

import csv
import io
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.middleware.auth import get_current_user
from src.api.routes.webhook import _validate_webhook_url
from src.models.scraped_data import ScrapedData

router = APIRouter(prefix="/export", tags=["export"])

# Flattened CSV columns — metadata fields extracted into proper columns
_CSV_FIELDNAMES = [
    "domain", "data_type", "url", "title", "published_date", "scraped_at",
    # Article
    "author", "excerpt", "word_count", "categories", "content",
    # Contact
    "first_name", "last_name", "job_title", "email", "phone", "linkedin_url",
    # Blog
    "total_articles",
    # Tech stack
    "platform", "frameworks", "js_libraries", "analytics",
    # Resource
    "resource_type", "description", "download_url",
    # Pricing
    "plan_name", "price", "billing_cycle", "features",
    "has_free_trial", "cta_text",
]



def _join_list(meta: dict, key: str) -> str:
    """Join a metadata list field into a semicolon-separated string."""
    val = meta.get(key, [])
    return "; ".join(val) if isinstance(val, list) else ""


def _flatten_row(item: ScrapedData) -> dict:
    """Flatten a ScrapedData record into a CSV-friendly dict."""
    meta = item.metadata or {}
    return {
        "domain": item.domain,
        "data_type": item.data_type,
        "url": item.url or "",
        "title": item.title or "",
        "published_date": str(item.published_date) if item.published_date else "",
        "scraped_at": item.scraped_at.isoformat() if item.scraped_at else "",
        # Article fields
        "author": meta.get("author", ""),
        "excerpt": meta.get("excerpt", ""),
        "word_count": meta.get("word_count", ""),
        "categories": _join_list(meta, "categories"),
        "content": meta.get("content", ""),
        # Contact fields
        "first_name": meta.get("first_name", ""),
        "last_name": meta.get("last_name", ""),
        "job_title": meta.get("job_title", ""),
        "email": meta.get("email", ""),
        "phone": meta.get("phone", ""),
        "linkedin_url": meta.get("linkedin_url", ""),
        # Blog fields
        "total_articles": meta.get("total_articles", ""),
        # Tech stack fields
        "platform": meta.get("platform", ""),
        "frameworks": _join_list(meta, "frameworks"),
        "js_libraries": _join_list(meta, "js_libraries"),
        "analytics": _join_list(meta, "analytics"),
        # Resource fields
        "resource_type": meta.get("resource_type", ""),
        "description": meta.get("description", ""),
        "download_url": meta.get("download_url", ""),
        # Pricing fields
        "plan_name": meta.get("plan_name", ""),
        "price": meta.get("price", ""),
        "billing_cycle": meta.get("billing_cycle", ""),
        "features": _join_list(meta, "features"),
        "has_free_trial": meta.get("has_free_trial", ""),
        "cta_text": meta.get("cta_text", ""),
    }



class WebhookConfig(BaseModel):
    """Configuration for webhook export."""

    url: str
    job_id: UUID | None = None
    domain: str | None = None


@router.get("/csv/{job_id}")
async def export_job_csv(job_id: UUID, user: dict = Depends(get_current_user)):
    """Export all scraped data from a job as CSV."""
    from src.db.pool import get_pool
    from src.db.queries.scraped_data import get_scraped_data_by_job

    pool = await get_pool()
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
async def export_all_csv(domain: str | None = Query(None), user: dict = Depends(get_current_user)):
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


@router.post("/webhook")
async def export_to_webhook(config: WebhookConfig, user: dict = Depends(get_current_user)):
    """Send scraped data to a webhook URL."""
    _validate_webhook_url(config.url)
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
