"""Webhook routes for n8n integration and external triggers."""

from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/webhook", tags=["webhook"])


class WebhookTriggerRequest(BaseModel):
    """Request body for triggering a scrape via webhook."""

    domain: str
    data_types: list[str] | None = None
    max_pages: int = 100
    template_id: str | None = None


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
async def trigger_scrape(request: WebhookTriggerRequest):
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
        request.data_types = ["blog_url", "article", "contact"]

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

    # TODO: Enqueue the job to arq worker
    # For now, just return the job ID

    return WebhookTriggerResponse(
        success=True,
        job_id=job.id,
        status_url=f"/jobs/{job.id}",
    )


@router.post("/test", response_model=WebhookTestResponse)
async def test_webhook(request: WebhookTestRequest):
    """
    Test a webhook URL by sending a test payload.

    This helps verify that the webhook endpoint is reachable before
    configuring it for automatic data export.
    """
    test_payload = {
        "source": "lake_b2b_scraper",
        "type": "test",
        "message": "This is a test webhook from Lake B2B Scraper",
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
async def webhook_callback(job_id: UUID, data: dict):
    """
    Receive callback data from external services.

    This endpoint can be used by n8n or other services to send data back
    to the scraper after processing.
    """
    # This is a placeholder for receiving processed data back
    # Could be used for:
    # - Receiving enriched data from n8n workflows
    # - Receiving validation results
    # - Receiving manual review decisions

    return {
        "success": True,
        "job_id": str(job_id),
        "received_keys": list(data.keys()),
    }
