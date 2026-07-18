"""Company enrichment models (v2.1)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CompanyProfile(BaseModel):
    id: UUID | None = None
    domain: str
    name: str | None = None
    description: str | None = None
    industry: str | None = None
    naics_code: str | None = None
    sic_code: str | None = None
    employee_range: str | None = None
    revenue_range: str | None = None
    logo_url: str | None = None
    location: str | None = None
    socials: dict = {}
    source: str = "scrape"
    fetched_at: datetime | None = None
