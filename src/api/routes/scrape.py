from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request

from src.db.pool import get_pool
from src.db.queries import jobs as job_queries
from src.db.queries import scraped_data as data_queries
from src.models.api import ExecuteScrapeResponse, ScrapeStatusResponse
from src.models.job import ScrapeJobInput

logger = structlog.get_logger()

router = APIRouter(prefix="/scrape")


@router.post("/execute", status_code=202, response_model=ExecuteScrapeResponse)
async def execute_scrape(input: ScrapeJobInput, request: Request) -> ExecuteScrapeResponse:
    pool = await get_pool()

    # Get org_id and user_id from authenticated user (set by auth middleware)
    org_id_str = getattr(request.state, "org_id", None)
    org_id = UUID(org_id_str) if org_id_str else None
    user_id_str = getattr(request.state, "user_id", None)
    user_id = UUID(user_id_str) if user_id_str else None

    # Create job record
    job = await job_queries.create_job(pool, input, org_id=org_id, user_id=user_id)

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
            tier=input.tier,
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


@router.post("/youtube-transcript")
async def youtube_transcript(request: Request):
    """Extract transcript from a YouTube video URL. Returns immediately (no job queue)."""
    from src.services.youtube import (
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
        extract_video_id,
        fetch_transcript,
        fetch_video_metadata,
    )

    body = await request.json()
    url = body.get("url", "").strip()
    include_timestamps = body.get("include_timestamps", True)
    languages = body.get("languages")

    if not url:
        return {"success": False, "error": "URL is required"}

    video_id = extract_video_id(url)
    if not video_id:
        return {"success": False, "error": "Invalid YouTube URL"}

    # Fetch metadata (best-effort)
    metadata = {"title": "", "channel": "", "channel_url": "", "thumbnail_url": ""}
    try:
        metadata = await fetch_video_metadata(video_id)
    except Exception as e:
        logger.warning("youtube_metadata_failed", video_id=video_id, error=str(e))

    # Fetch transcript
    try:
        transcript_data = fetch_transcript(video_id, languages=languages)
    except TranscriptsDisabled:
        return {
            "success": False,
            "error": "No transcript available — captions are disabled for this video",
            "metadata": metadata,
            "video_id": video_id,
        }
    except (NoTranscriptFound, VideoUnavailable) as e:
        return {"success": False, "error": str(e), "video_id": video_id}
    except Exception as e:
        logger.error("youtube_transcript_failed", video_id=video_id, error=str(e))
        return {"success": False, "error": f"Failed to fetch transcript: {e}"}

    result = {
        "success": True,
        "video_id": video_id,
        "metadata": metadata,
        "transcript_text": transcript_data["transcript_text"],
        "segment_count": transcript_data["segment_count"],
        "language": transcript_data["language"],
        "language_code": transcript_data["language_code"],
        "is_generated": transcript_data["is_generated"],
        "duration_seconds": transcript_data["duration_seconds"],
    }

    if include_timestamps:
        result["segments"] = transcript_data["segments"]

    return result
