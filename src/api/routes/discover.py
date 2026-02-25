"""API routes for the discovery pipeline (search-to-scrape)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from src.api.middleware.auth import get_current_user
from src.db.pool import get_pool
from src.db.queries import discovery as disc_queries
from src.db.queries import jobs as job_queries
from src.models.discovery import (
    ChildJobStatus,
    DiscoverSearchResponse,
    DiscoveryJobInput,
    DiscoveryStatusResponse,
    TrackedSearchInput,
    TrackedSearchResponse,
)

router = APIRouter(prefix="/discover", tags=["discover"])


@router.post("/search", status_code=202, response_model=DiscoverSearchResponse)
async def discover_search(
    input: DiscoveryJobInput,
    user: dict = Depends(get_current_user),
) -> DiscoverSearchResponse:
    """Submit a search-driven discovery + scrape job."""
    pool = await get_pool()
    org_id = user["org_id"]

    disc_job = await disc_queries.create_discovery_job(pool, input, org_id)

    # Enqueue the discovery job via arq
    try:
        from arq.connections import RedisSettings
        from arq.connections import create_pool as create_arq_pool

        from src.config.settings import get_settings

        settings = get_settings()
        redis = await create_arq_pool(RedisSettings.from_dsn(settings.redis_url))
        await redis.enqueue_job(
            "process_discovery_job",
            discovery_id=str(disc_job.id),
        )
        await redis.aclose()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to enqueue discovery job: {e}")

    return DiscoverSearchResponse(
        discovery_id=disc_job.id,
        query=input.query,
        status=disc_job.status,
        message="Discovery job queued. Searching for domains...",
    )


@router.get("/status/{discovery_id}", response_model=DiscoveryStatusResponse)
async def discover_status(
    discovery_id: UUID,
    user: dict = Depends(get_current_user),
) -> DiscoveryStatusResponse:
    """Get status of a discovery job including all child scrape jobs."""
    pool = await get_pool()

    disc_job = await disc_queries.get_discovery_job(pool, discovery_id)
    if disc_job is None:
        raise HTTPException(status_code=404, detail="Discovery job not found")

    # Load child domain entries with their scrape job status
    domains = await disc_queries.get_discovery_domains(pool, discovery_id)

    child_jobs: list[ChildJobStatus] = []
    domains_scraped = 0
    domains_skipped = 0
    domains_pending = 0
    total_cost = 0.0

    for d in domains:
        pages_scraped = 0
        cost_usd = 0.0

        if d.scrape_job_id:
            scrape_job = await job_queries.get_job(pool, d.scrape_job_id)
            if scrape_job:
                pages_scraped = scrape_job.pages_scraped
                cost_usd = scrape_job.cost_usd
                total_cost += cost_usd

        if d.status == "completed":
            domains_scraped += 1
        elif d.status == "skipped":
            domains_skipped += 1
        elif d.status in ("pending", "scraping"):
            domains_pending += 1

        child_jobs.append(
            ChildJobStatus(
                job_id=d.scrape_job_id,
                domain=d.domain,
                status=d.status,
                skip_reason=d.skip_reason,
                pages_scraped=pages_scraped,
                cost_usd=cost_usd,
            )
        )

    # Count search results from stored JSONB
    search_results_count = 0
    if isinstance(disc_job.search_results, list):
        search_results_count = len(disc_job.search_results)

    return DiscoveryStatusResponse(
        discovery_id=disc_job.id,
        query=disc_job.query,
        status=disc_job.status,
        domains_found=disc_job.domains_found,
        domains_scraped=domains_scraped,
        domains_skipped=domains_skipped,
        domains_pending=domains_pending,
        search_results_count=search_results_count,
        child_jobs=child_jobs,
        total_cost_usd=total_cost,
        created_at=disc_job.created_at.isoformat(),
        completed_at=disc_job.completed_at.isoformat() if disc_job.completed_at else None,
    )


@router.post("/tracked", status_code=201, response_model=TrackedSearchResponse)
async def create_tracked_search(
    input: TrackedSearchInput,
    user: dict = Depends(get_current_user),
) -> TrackedSearchResponse:
    """Set up a recurring search-to-scrape schedule."""
    pool = await get_pool()
    org_id = user["org_id"]

    tracked = await disc_queries.create_tracked_search(pool, input, org_id)

    return TrackedSearchResponse(
        tracked_search_id=tracked.id,
        query=tracked.query,
        scrape_frequency=tracked.scrape_frequency,
        next_run_at=tracked.next_run_at.isoformat() if tracked.next_run_at else None,
        is_active=tracked.is_active,
    )


@router.get("/tracked", response_model=list[TrackedSearchResponse])
async def list_tracked_searches(
    user: dict = Depends(get_current_user),
) -> list[TrackedSearchResponse]:
    """List all tracked searches."""
    pool = await get_pool()
    searches = await disc_queries.list_tracked_searches(pool)

    return [
        TrackedSearchResponse(
            tracked_search_id=s.id,
            query=s.query,
            scrape_frequency=s.scrape_frequency,
            next_run_at=s.next_run_at.isoformat() if s.next_run_at else None,
            is_active=s.is_active,
        )
        for s in searches
    ]


@router.delete("/tracked/{tracked_search_id}")
async def delete_tracked_search(
    tracked_search_id: UUID,
    user: dict = Depends(get_current_user),
) -> dict:
    """Stop a tracked search (soft delete)."""
    pool = await get_pool()

    tracked = await disc_queries.get_tracked_search(pool, tracked_search_id)
    if tracked is None:
        raise HTTPException(status_code=404, detail="Tracked search not found")

    await disc_queries.delete_tracked_search(pool, tracked_search_id)
    return {"success": True}
