from datetime import date, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class DataType(StrEnum):
    BLOG_URL = "blog_url"
    ARTICLE = "article"
    RESOURCE = "resource"
    CONTACT = "contact"
    TECH_STACK = "tech_stack"
    PRICING = "pricing"
    PAGE = "page"  # Uncategorized pages — not sent to content workers
    DOCUMENT = "document"  # PDF/DOCX documents
    EXTRACTED = "extracted"  # Schema-based extraction results
    WEBHOOK_CALLBACK = "webhook_callback"  # Inbound n8n / external callback payloads



class ScrapedData(BaseModel):
    model_config = {"extra": "ignore"}

    id: UUID
    job_id: UUID
    domain: str
    data_type: DataType
    org_id: UUID | None = None
    user_id: UUID | None = None
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
    content: str | None = None


class ContactMetadata(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    job_title: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    source: str = ""


class DetectedTech(BaseModel):
    """One technology detection, with the evidence that produced it.

    `evidence_type` names the signal the match came from (header, cookie,
    script URL, meta, dns, cert, body text, or `implies`). `recommended` is
    the "safe to act on" flag: structural and body-text matches qualify, the
    weaker fallback tier does not.
    """

    name: str
    category: str
    confidence: Literal["high", "medium", "low"]
    evidence: str
    evidence_type: str
    version: str | None = None
    recommended: bool = False


class TechStackMetadata(BaseModel):
    """Page-level tech signals (from the homepage's HTML/headers/cookies).

    Domain-level facts — web hosting, email hosting, SSL certificate — are
    NOT here: they don't vary per page and are resolved via DNS/TLS once per
    domain, cached on CompanyProfile (see src/models/company.py, /api/enrich).
    """

    platform: str | None = None
    frameworks: list[str] = []
    js_libraries: list[str] = []
    analytics: list[str] = []
    marketing_tools: list[str] = []
    cdn: list[str] = []
    widgets: list[str] = []
    web_servers: list[str] = []
    programming_languages: list[str] = []
    server_os: str | None = None
    # v2.2 — additional BuiltWith-comparable categories
    databases: list[str] = []
    seo_tools: list[str] = []
    security: list[str] = []
    ecommerce: list[str] = []
    payment_processors: list[str] = []
    # Categories carried over from the tech-detection-accuracy catalog
    build_tools: list[str] = []
    fonts: list[str] = []
    auth: list[str] = []
    monitoring: list[str] = []
    search: list[str] = []
    # v2.3 — depth-program categories
    advertising: list[str] = []
    ab_testing: list[str] = []
    crm: list[str] = []
    video: list[str] = []
    maps: list[str] = []
    translation: list[str] = []
    accessibility: list[str] = []
    # Anything the catalog detected whose category has no dedicated field —
    # including the domain-level facts (hosting, email hosting, SSL) that are
    # reported properly on CompanyProfile.
    other_technologies: list[str] = []
    # Per-detection audit trail: name, category, confidence, version,
    # evidence snippet, and which signal produced it.
    detections: list[DetectedTech] = []


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


class DocumentMetadata(BaseModel):
    source_type: str = "pdf"  # pdf, docx
    page_count: int = 0
    author: str | None = None
    tables: list[list[list[str]]] = []
    word_count: int = 0
    text_content: str = ""


# --- Ingest API models (Chrome extension, external tools) ---


class IngestRecord(BaseModel):
    data_type: str  # contact, article, tech_stack, etc.
    url: str | None = None
    title: str | None = None
    metadata: dict = {}  # type: ignore[assignment]


class IngestPayload(BaseModel):
    domain: str = Field(min_length=1)
    source: str = "chrome_extension"  # stored as strategy_used on the virtual job
    records: list[IngestRecord] = Field(..., min_length=1, max_length=500)

