"""Ingest API — accept pre-scraped data from Chrome extension or external tools.

Creates a "virtual job" in scrape_jobs so ingested data fits the existing schema
(scraped_data.job_id FK constraint) and triggers pg_notify for n8n enrichment.
"""

from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, HTTPException, Request

from src.db.pool import get_pool
from src.db.queries.scraped_data import batch_insert_scraped_data
from src.models.scraped_data import IngestPayload

router = APIRouter(prefix="/ingest")
log = structlog.get_logger()


@router.post("", status_code=201)
async def ingest_data(request: Request, payload: IngestPayload):
    """Accept pre-scraped data from Chrome extension or external tools.

    Creates a virtual scrape_job (immediately completed) and inserts records.
    The pg_notify trigger fires on insert, feeding n8n enrichment pipelines.
    """
    org_id = getattr(request.state, "org_id", None)
    if not org_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = getattr(request.state, "user_id", None)
    pool = await get_pool()

    # Create virtual job (immediately completed, no actual scraping)
    job_id = uuid4()
    await pool.execute(
        """INSERT INTO scrape_jobs
           (id, domain, template_id, status, strategy_used,
            pages_scraped, org_id, user_id, completed_at)
           VALUES ($1, $2, 'extension', 'completed',
                   $3, $4, $5, $6, NOW())""",
        job_id,
        payload.domain,
        payload.source,
        len(payload.records),
        UUID(org_id),
        UUID(user_id) if user_id else None,
    )

    # Batch insert records
    records = [
        {
            "job_id": job_id,
            "domain": payload.domain,
            "data_type": r.data_type,
            "url": r.url,
            "title": r.title,
            "metadata": r.metadata,
            "org_id": UUID(org_id),
            "user_id": UUID(user_id) if user_id else None,
        }
        for r in payload.records
    ]
    count = await batch_insert_scraped_data(pool, records)

    log.info(
        "data_ingested",
        job_id=str(job_id),
        domain=payload.domain,
        source=payload.source,
        records=count,
        org_id=org_id,
    )

    return {"job_id": str(job_id), "records_ingested": count, "domain": payload.domain}
