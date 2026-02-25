from uuid import UUID

from pydantic import BaseModel


class ExecuteScrapeResponse(BaseModel):
    job_id: UUID
    status: str
    message: str


class ScrapeStatusResponse(BaseModel):
    job_id: UUID
    domain: str
    status: str
    strategy_used: str | None = None
    pages_scraped: int = 0
    cost_usd: float = 0.0
    duration_ms: int | None = None
    created_at: str
    completed_at: str | None = None
    error_message: str | None = None
    data_count: int = 0


class HealthResponse(BaseModel):
    status: str
    database: str
    redis: str
    lakecurrent: str = "disabled"


class DomainStatsResponse(BaseModel):
    domain: str
    last_successful_strategy: str | None = None
    block_count: int = 0
    success_rate: float | None = None
    avg_cost_usd: float | None = None
    total_jobs: int = 0
    total_data_points: int = 0
