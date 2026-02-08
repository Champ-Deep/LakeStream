from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class DataType(StrEnum):
    BLOG_URL = "blog_url"
    ARTICLE = "article"
    RESOURCE = "resource"
    CONTACT = "contact"
    TECH_STACK = "tech_stack"
    PRICING = "pricing"


class ScrapedData(BaseModel):
    id: UUID
    job_id: UUID
    domain: str
    data_type: DataType
    url: str | None = None
    title: str | None = None
    published_date: date | None = None
    metadata: dict = {}  # type: ignore[assignment]
    scraped_at: datetime


class BlogUrlMetadata(BaseModel):
    blog_landing_url: str
    article_urls: list[str] = []
    total_articles: int = 0


class ArticleMetadata(BaseModel):
    author: str | None = None
    categories: list[str] = []
    word_count: int = 0
    excerpt: str | None = None


class ContactMetadata(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    job_title: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    source: str = ""


class TechStackMetadata(BaseModel):
    platform: str | None = None
    js_libraries: list[str] = []
    analytics: list[str] = []
    marketing_tools: list[str] = []
    frameworks: list[str] = []


class ResourceMetadata(BaseModel):
    resource_type: str = ""
    description: str | None = None
    gated: bool = False
    download_url: str | None = None


class PricingMetadata(BaseModel):
    """Metadata for pricing pages."""

    plan_name: str
    price: str | None = None
    billing_cycle: str = "unknown"  # monthly, annual, quarterly, unknown
    features: list[str] = []
    has_free_trial: bool = False
    cta_text: str | None = None
