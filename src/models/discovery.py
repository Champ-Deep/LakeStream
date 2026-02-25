"""Pydantic models for the discovery pipeline (search-to-scrape)."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class DiscoveryStatus(StrEnum):
    SEARCHING = "searching"
    SCRAPING = "scraping"
    COMPLETED = "completed"
    FAILED = "failed"


# --------------- Request models ---------------


class DiscoveryJobInput(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    search_mode: str = Field(default="auto", pattern=r"^(auto|filter|glimpse)$")
    search_pages: int = Field(default=3, ge=1, le=10)
    results_per_page: int = Field(default=10, ge=1, le=50)
    data_types: list[str] = Field(min_length=1)
    template_id: str = Field(default="generic")
    max_pages_per_domain: int = Field(default=50, ge=1, le=500)
    priority: int = Field(default=5, ge=1, le=10)


class TrackedSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    search_mode: str = Field(default="auto", pattern=r"^(auto|filter|glimpse)$")
    search_pages: int = Field(default=2, ge=1, le=10)
    results_per_page: int = Field(default=10, ge=1, le=50)
    data_types: list[str] = Field(min_length=1)
    template_id: str = Field(default="generic")
    max_pages_per_domain: int = Field(default=50, ge=1, le=500)
    scrape_frequency: str = Field(default="weekly", pattern=r"^(daily|weekly|biweekly|monthly)$")
    webhook_url: str | None = None


# --------------- DB row models ---------------


class DiscoveryJob(BaseModel):
    id: UUID
    org_id: UUID
    query: str
    search_mode: str
    search_pages: int
    results_per_page: int
    data_types: list[str]
    template_id: str
    max_pages_per_domain: int
    status: DiscoveryStatus
    domains_found: int = 0
    domains_skipped: int = 0
    search_results: dict | list | None = None
    error_message: str | None = None
    total_cost_usd: float = 0.0
    created_at: datetime
    completed_at: datetime | None = None


class DiscoveryJobDomain(BaseModel):
    id: UUID
    discovery_id: UUID
    domain: str
    scrape_job_id: UUID | None = None
    source_url: str
    source_title: str | None = None
    source_snippet: str | None = None
    source_score: float | None = None
    status: str = "pending"
    skip_reason: str | None = None
    created_at: datetime


class TrackedSearch(BaseModel):
    id: UUID
    org_id: UUID
    query: str
    search_mode: str
    search_pages: int
    results_per_page: int
    data_types: list[str]
    template_id: str
    max_pages_per_domain: int
    scrape_frequency: str
    webhook_url: str | None = None
    is_active: bool = True
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    total_runs: int = 0
    total_domains_discovered: int = 0
    created_at: datetime


# --------------- API response models ---------------


class DiscoverSearchResponse(BaseModel):
    discovery_id: UUID
    query: str
    status: str
    message: str


class ChildJobStatus(BaseModel):
    job_id: UUID | None = None
    domain: str
    status: str
    skip_reason: str | None = None
    pages_scraped: int = 0
    cost_usd: float = 0.0


class DiscoveryStatusResponse(BaseModel):
    discovery_id: UUID
    query: str
    status: str
    domains_found: int = 0
    domains_scraped: int = 0
    domains_skipped: int = 0
    domains_pending: int = 0
    search_results_count: int = 0
    child_jobs: list[ChildJobStatus] = []
    total_cost_usd: float = 0.0
    created_at: str
    completed_at: str | None = None


class TrackedSearchResponse(BaseModel):
    tracked_search_id: UUID
    query: str
    scrape_frequency: str
    next_run_at: str | None = None
    is_active: bool = True
