"""Shared webhook export helper for sending job results to external webhooks."""

from uuid import UUID

import httpx
import structlog

log = structlog.get_logger()


async def export_job_to_webhook(job_id: UUID, webhook_url: str) -> bool:
    """Send all scraped data from a job to a webhook URL.

    Returns True if the webhook accepted the payload (status < 400).
    """
    from src.db.pool import get_pool
    from src.db.queries.scraped_data import get_scraped_data_by_job

    pool = await get_pool()
    data = await get_scraped_data_by_job(pool, job_id)

    if not data:
        log.info("webhook_export_skipped", job_id=str(job_id), reason="no_data")
        return True

    payload = {
        "source": "lake_b2b_scraper",
        "trigger": "scheduled",
        "job_id": str(job_id),
        "count": len(data),
        "data": [
            {
                "id": str(item.id),
                "domain": item.domain,
                "data_type": item.data_type,
                "url": item.url,
                "title": item.title,
                "metadata": item.metadata,
                "scraped_at": item.scraped_at.isoformat()
                if item.scraped_at
                else None,
            }
            for item in data
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                webhook_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Lake-B2B-Scraper/1.0",
                },
            )
            success = response.status_code < 400
            log.info(
                "webhook_export_sent",
                job_id=str(job_id),
                webhook_url=webhook_url,
                status=response.status_code,
                records=len(data),
                success=success,
            )
            return success
    except Exception:
        log.exception(
            "webhook_export_failed",
            job_id=str(job_id),
            webhook_url=webhook_url,
        )
        return False
