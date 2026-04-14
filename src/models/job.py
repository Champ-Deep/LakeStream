from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScrapeJobInput(BaseModel):
    domain: str = Field(min_length=3)
    template_id: str | None = None
    tier: str | None = Field(
        default=None,
        description="Optional tier override (lightpanda, playwright, playwright_proxy). "
        "Defaults to None — escalation service selects automatically.",
    )
    max_pages: int = Field(default=100, gt=0, le=500)
    data_types: list[str] = Field(
        default=["blog_url", "article", "contact", "tech_stack", "resource", "pricing"]
    )
    raw_only: bool = Field(
        default=False,
        description="Save only raw page content (skip article/contact/tech extraction).",
    )
    region: str | None = Field(
        default=None,
        description="Geo-target region for proxy selection: us, eu, uk, de, asia, in, au.",
    )
    extraction_schema: dict | None = Field(
        default=None,
        description="Custom extraction schema. If set, runs schema extractor on every page.",
    )
    extraction_mode: str = Field(
        default="css",
        description="Extraction mode: css, ai, auto. Only used with extraction_schema.",
    )
    priority: int = Field(default=5, ge=1, le=10)
    llm_mode: str = Field(
        default="off",
        description=(
            "AI extraction mode: "
            "'off' = CSS only (default, free), "
            "'fallback' = CSS + LLM on every page (hybrid, higher quality), "
            "'only' = LLM only on every page (highest quality, highest cost)."
        ),
    )


class ScrapeJob(BaseModel):
    model_config = {"extra": "ignore"}

    id: UUID
    domain: str
    template_id: str
    status: JobStatus
    org_id: UUID | None = None
    user_id: UUID | None = None
    strategy_used: str | None = None
    error_message: str | None = None
    cost_usd: float = 0.0
    duration_ms: int | None = None
    pages_scraped: int = 0
    retry_count: int = 0
    created_at: datetime
    completed_at: datetime | None = None


class JobResult(BaseModel):
    job_id: UUID
    domain: str
    strategy_used: str
    pages_scraped: int
    data_extracted: int
    duration_ms: int
    errors: list[str] = []
