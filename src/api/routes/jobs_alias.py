"""GET /api/jobs/{job_id} — convenience alias for job status + result links.

The enrichment-pipeline playbook polls this exact path; it mirrors
/scrape/status/{job_id} and adds export links so a caller can pull the
scraped markdown/data in one hop once the job completes.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException

from src.db.pool import get_pool
from src.db.queries import jobs as job_queries
from src.db.queries import scraped_data as data_queries

router = APIRouter(prefix="/jobs")


@router.get("/{job_id}")
async def get_job_status(job_id: UUID) -> dict:
    pool = await get_pool()
    job = await job_queries.get_job(pool, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    data_count = await data_queries.count_scraped_data_by_job(pool, job_id)
    return {
        "job_id": str(job.id),
        "domain": job.domain,
        "status": job.status,
        "strategy_used": job.strategy_used,
        "pages_scraped": job.pages_scraped,
        "cost_usd": job.cost_usd,
        "duration_ms": job.duration_ms,
        "created_at": job.created_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "error_message": job.error_message,
        "data_count": data_count,
        "results": {
            "json": f"/api/exports/json/{job.id}",
            "csv": f"/api/exports/csv/{job.id}",
        },
    }
