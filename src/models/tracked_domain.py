from datetime import datetime

from pydantic import BaseModel, Field


class TrackedDomain(BaseModel):
    domain: str
    data_types: list[str] = Field(
        default=["blog_url", "article", "contact", "tech_stack", "resource", "pricing"]
    )
    scrape_frequency: str = "weekly"
    max_pages: int = 100
    template_id: str = "auto"
    webhook_url: str | None = None
    is_active: bool = True
    last_auto_scraped_at: datetime | None = None
    next_scrape_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AddSiteInput(BaseModel):
    domain: str = Field(min_length=3)
    data_types: list[str] = Field(
        default=["blog_url", "article", "contact", "tech_stack", "resource", "pricing"]
    )
    scrape_frequency: str = Field(default="weekly")
    max_pages: int = Field(default=100, gt=0, le=500)
    webhook_url: str | None = None
