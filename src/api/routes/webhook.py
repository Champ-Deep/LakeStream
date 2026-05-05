"""Webhook routes for n8n integration and external triggers."""

import ipaddress
import json
import socket
from urllib.parse import urlparse
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.middleware.auth import authorize_resource, get_current_user

router = APIRouter(prefix="/webhook", tags=["webhook"])

log = structlog.get_logger()

# Cap callback payload size — n8n workflows can produce large dicts and we
# don't want a single misbehaving workflow to fill the DB.
_MAX_CALLBACK_PAYLOAD_BYTES = 256 * 1024  # 256 KiB


def _validate_webhook_url(url: str) -> None:
    """Reject URLs that point to private/internal network addresses (SSRF prevention)."""
    try:
        parsed = urlparse(url)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid URL")

    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="URL must use http or https")

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(status_code=400, detail="Invalid URL: missing hostname")

    try:
        ip = ipaddress.ip_address(socket.gethostbyname(hostname))
    except (socket.gaierror, ValueError):
        raise HTTPException(status_code=400, detail="Could not resolve hostname")

    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise HTTPException(status_code=400, detail="URL resolves to a private/internal address")


class WebhookTriggerRequest(BaseModel):
    """Request body for triggering a scrape via webhook."""

    domain: str
    data_types: list[str] | None = None
    max_pages: int = 100
    template_id: str | None = None
    tier: str | None = None


class WebhookTriggerResponse(BaseModel):
    """Response for webhook trigger."""

    success: bool
    job_id: UUID
    status_url: str


class WebhookTestRequest(BaseModel):
    """Request body for testing a webhook URL."""

    url: str


class WebhookTestResponse(BaseModel):
    """Response for webhook test."""

    success: bool
    status_code: int | None = None
    message: str


@router.post("/trigger", response_model=WebhookTriggerResponse)
async def trigger_scrape(request: WebhookTriggerRequest, user: dict = Depends(get_current_user)):
    """
    Trigger a scrape job via webhook.

    This endpoint is designed for n8n integration. Send a POST request with the
    domain and optional parameters to start a scrape job.

    Example n8n HTTP Request node configuration:
    - Method: POST
    - URL: http://your-server:3001/api/webhook/trigger
    - Body: {"domain": "example.com", "data_types": ["blog_url", "article"]}
    """
    from src.db.pool import get_pool
    from src.db.queries.jobs import create_job
    from src.models.job import ScrapeJobInput

    # Default data types if not provided
    if not request.data_types:
        request.data_types = [
            "blog_url",
            "article",
            "contact",
            "tech_stack",
            "resource",
            "pricing",
        ]

    # Create job input
    job_input = ScrapeJobInput(
        domain=request.domain,
        data_types=request.data_types,
        max_pages=request.max_pages,
        template_id=request.template_id,
    )

    # Create the job
    pool = await get_pool()
    job = await create_job(pool, job_input)

    # Enqueue the job to arq worker
    try:
        from arq.connections import RedisSettings
        from arq.connections import create_pool as create_arq_pool

        from src.config.settings import get_settings

        settings = get_settings()
        redis = await create_arq_pool(RedisSettings.from_dsn(settings.redis_url))
        await redis.enqueue_job(
            "process_scrape_job",
            job_id=str(job.id),
            domain=request.domain,
            template_id=request.template_id or "auto",
            max_pages=request.max_pages,
            data_types=request.data_types,
            tier=request.tier,
            raw_only=getattr(request, "raw_only", False),
        )
        await redis.aclose()
    except Exception as e:
        import structlog

        log = structlog.get_logger()
        log.error("webhook_enqueue_failed", job_id=str(job.id), error=str(e))
        # Job created but not enqueued - will remain in PENDING state

    return WebhookTriggerResponse(
        success=True,
        job_id=job.id,
        status_url=f"/jobs/{job.id}",
    )


@router.post("/test", response_model=WebhookTestResponse)
async def test_webhook(request: WebhookTestRequest, user: dict = Depends(get_current_user)):
    """
    Test a webhook URL by sending a test payload.

    This helps verify that the webhook endpoint is reachable before
    configuring it for automatic data export.
    """
    _validate_webhook_url(request.url)
    test_payload = {
        "source": "lake_b2b_scraper",
        "type": "test",
        "message": "This is a test webhook from LakeStream",
        "timestamp": "2024-01-01T00:00:00Z",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                request.url,
                json=test_payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Lake-B2B-Scraper/1.0",
                },
            )

            if response.status_code < 400:
                return WebhookTestResponse(
                    success=True,
                    status_code=response.status_code,
                    message=f"Webhook responded with status {response.status_code}",
                )
            else:
                return WebhookTestResponse(
                    success=False,
                    status_code=response.status_code,
                    message=f"Webhook returned error status {response.status_code}",
                )

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Webhook request timed out")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect: {e!s}")


@router.post("/callback/{job_id}")
async def webhook_callback(
    job_id: UUID,
    data: dict,
    user: dict = Depends(get_current_user),
):
    """Receive callback data from external services (n8n workflows, etc.).

    Persists the payload as a scraped_data row with data_type='webhook_callback',
    scoped to the job's org. This makes enrichment results, validation outcomes,
    and manual review decisions queryable from the dashboard alongside the
    scraper's own output.

    Authorization: caller must own the job, or be admin (404 otherwise).

    Body: any JSON dict. Stored verbatim under metadata.payload. Optional
    well-known top-level keys are extracted for indexing:
      - source (str): "n8n", "manual_review", etc. — stored as metadata.source
      - url (str): becomes the row's url column
      - title (str): becomes the row's title column

    Returns: {success, job_id, record_id}.
    """
    from src.db.pool import get_pool
    from src.db.queries.scraped_data import insert_scraped_data

    # Reject oversized payloads early — protects the DB from one bad workflow.
    payload_size = len(json.dumps(data, default=str).encode("utf-8"))
    if payload_size > _MAX_CALLBACK_PAYLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Callback payload too large ({payload_size} bytes); "
                f"max {_MAX_CALLBACK_PAYLOAD_BYTES} bytes"
            ),
        )

    pool = await get_pool()

    # Authorize the caller against the target job. We fetch a thin row rather
    # than the full ScrapeJob model — we only need org_id, user_id, domain.
    row = await pool.fetchrow(
        "SELECT domain, org_id, user_id FROM scrape_jobs WHERE id = $1", job_id
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

    # Pull a few well-known fields up so the dashboard's URL/title columns
    # show something useful. Everything else stays in metadata.payload.
    callback_url = data.get("url") if isinstance(data.get("url"), str) else None
    callback_title = data.get("title") if isinstance(data.get("title"), str) else None
    source = data.get("source") if isinstance(data.get("source"), str) else "webhook"

    metadata = {
        "source": source,
        "payload": data,
    }

    record_id = await insert_scraped_data(
        pool,
        job_id=job_id,
        domain=row["domain"],
        data_type="webhook_callback",
        url=callback_url,
        title=callback_title,
        metadata=metadata,
        org_id=row["org_id"],
    )

    log.info(
        "webhook_callback_received",
        job_id=str(job_id),
        record_id=str(record_id),
        source=source,
        bytes=payload_size,
    )

    return {
        "success": True,
        "job_id": str(job_id),
        "record_id": str(record_id),
    }
