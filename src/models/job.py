from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ScrapeJobInput(BaseModel):
    domain: str = Field(min_length=3)
    template_id: str | None = None
    max_pages: int = Field(default=100, gt=0, le=500)
    data_types: list[str] = Field(default=["blog_url", "article"])
    priority: int = Field(default=5, ge=1, le=10)


class ScrapeJob(BaseModel):
    id: UUID
    domain: str
    template_id: str
    status: JobStatus
    strategy_used: str | None = None
    error_message: str | None = None
    cost_usd: float = 0.0
    duration_ms: int | None = None
    pages_scraped: int = 0
    created_at: datetime
    completed_at: datetime | None = None


class JobResult(BaseModel):
    job_id: UUID
    domain: str
    strategy_used: str
    pages_scraped: int
    data_extracted: int
    cost_usd: float
    duration_ms: int
    errors: list[str] = []
