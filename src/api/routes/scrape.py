from uuid import UUID

from fastapi import APIRouter, HTTPException

from src.db.pool import get_pool
from src.db.queries import jobs as job_queries
from src.db.queries import scraped_data as data_queries
from src.models.api import ExecuteScrapeResponse, ScrapeStatusResponse
from src.models.job import ScrapeJobInput

router = APIRouter(prefix="/scrape")


@router.post("/execute", status_code=202, response_model=ExecuteScrapeResponse)
async def execute_scrape(input: ScrapeJobInput) -> ExecuteScrapeResponse:
    pool = await get_pool()

    # Create job record
    job = await job_queries.create_job(pool, input)

    # Enqueue arq job
    try:
        from arq.connections import RedisSettings
        from arq.connections import create_pool as create_arq_pool

        from src.config.settings import get_settings

        settings = get_settings()
        redis = await create_arq_pool(RedisSettings.from_dsn(settings.redis_url))
        await redis.enqueue_job(
            "process_scrape_job",
            job_id=str(job.id),
            domain=input.domain,
            template_id=input.template_id or "auto",
            max_pages=input.max_pages,
            data_types=input.data_types,
        )
        await redis.aclose()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to enqueue job: {e}")

    return ExecuteScrapeResponse(
        job_id=job.id,
        status=job.status,
        message=f"Scrape job queued for {input.domain}",
    )


@router.get("/status/{job_id}", response_model=ScrapeStatusResponse)
async def get_status(job_id: UUID) -> ScrapeStatusResponse:
    pool = await get_pool()
    job = await job_queries.get_job(pool, job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    data_count = await data_queries.count_scraped_data_by_job(pool, job_id)

    return ScrapeStatusResponse(
        job_id=job.id,
        domain=job.domain,
        status=job.status,
        strategy_used=job.strategy_used,
        pages_scraped=job.pages_scraped,
        cost_usd=job.cost_usd,
        duration_ms=job.duration_ms,
        created_at=job.created_at.isoformat(),
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        error_message=job.error_message,
        data_count=data_count,
    )
