"""Data-type extraction strategies, extracted 1:1 from ``ContentWorker``.

Each function here takes the already-fetched/parsed HTML (or fetch result,
for PDFs) and returns the plain-dict record(s) ContentWorker used to build
inline. None of these functions know about job orchestration, cancellation,
heartbeats, or persistence (``export_results``) — they are pure extraction
logic, safe to unit test without a DB pool or network access.

Behavior is preserved exactly from the original ``ContentWorker`` private
methods; only the code's location and dependencies (passed-in ``job_id``/
``domain``/``logger`` instead of ``self``) changed.
"""

from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import structlog

from src.models.scraped_data import (
    ArticleMetadata,
    BlogUrlMetadata,
    ContactMetadata,
    DataType,
    PricingMetadata,
    ResourceMetadata,
    TechStackMetadata,
)
from src.scraping.parser.contact_parser import ContactParser
from src.scraping.parser.html_parser import HtmlParser
from src.scraping.parser.pricing_parser import PricingParser
from src.scraping.parser.resource_parser import ResourceParser
from src.scraping.parser.tech_engine import (
    detect as detect_tech,
    detections_to_metadata,
    extract_page_signals,
)
from src.templates.base import BaseTemplate
from src.utils.url import extract_domain

log = structlog.get_logger()

# Minimum word count to treat a page as having article-worthy content
MIN_ARTICLE_WORDS = 200

_SKIP_EXTENSIONS = frozenset({
    ".doc", ".docx", ".zip", ".png", ".jpg", ".jpeg",
    ".gif", ".svg", ".webp", ".mp3", ".mp4", ".avi",
})
# PDF is handled separately — not skipped

_ERROR_MARKERS = ("error", "404", "not found", "page not found")


def is_error_page(title: str | None) -> bool:
    """True if a page's title matches one of the known error-page markers."""
    return bool(title and any(m in title.lower() for m in _ERROR_MARKERS))


def extract_page_record(
    job_id: str, domain: str, url: str, parser: HtmlParser, rich_meta: dict,
) -> dict:
    """Full page content record — saved for every successfully fetched page."""
    content = parser.extract_content()
    word_count = len(content.split()) if content else 0
    return {
        "job_id": UUID(job_id),
        "domain": domain,
        "data_type": DataType.PAGE,
        "url": url,
        "title": parser.extract_title(),
        "metadata": {**rich_meta, "content": content, "word_count": word_count},
    }


def extract_article_record(
    job_id: str,
    domain: str,
    url: str,
    html: str,
    parser: HtmlParser,
    rich_meta: dict,
    template: BaseTemplate | None = None,
    logger: Any | None = None,
) -> dict | None:
    """Article record for pages with substantial text. Ported from ArticleParserWorker.

    Content/word_count/categories always come from the generic
    ``HtmlParser`` — that is the full, reliable extraction. When a
    platform template matched, its ``extract_article`` selectors are
    used only to fill in a better ``title``/``author`` when they find a
    non-empty value; the template's own (much shorter) excerpt/content
    fields are intentionally ignored.
    """
    logger = logger or log
    content = parser.extract_content()
    word_count = parser.count_words()
    excerpt = parser.extract_meta("description")

    if word_count == 0 and excerpt is None:
        return None

    title = parser.extract_title()
    author = parser.extract_meta("author")

    if template is not None:
        try:
            template_result = template.extract_article(html, url)
        except Exception as e:
            logger.warning(
                "template_extract_article_failed",
                url=url,
                template=template.config.id,
                error=str(e),
            )
            template_result = {}
        title = template_result.get("title") or title
        author = template_result.get("author") or author

    metadata = ArticleMetadata(
        author=author,
        categories=parser.extract_categories(),
        word_count=word_count,
        excerpt=excerpt,
        content=content,
    )
    return {
        "job_id": UUID(job_id),
        "domain": domain,
        "data_type": DataType.ARTICLE,
        "url": url,
        "title": title,
        "metadata": {**rich_meta, **metadata.model_dump()},
    }


def filter_article_links(links: list[str], source_url: str) -> list[str]:
    """Remove homepage, non-HTML, and off-domain links. From BlogExtractorWorker."""
    source_domain = extract_domain(source_url)
    filtered = []
    for link in links:
        parsed = urlparse(link)
        path = parsed.path.rstrip("/")
        if not path:
            continue
        if any(path.lower().endswith(ext) for ext in _SKIP_EXTENSIONS):
            continue
        if extract_domain(link) != source_domain:
            continue
        filtered.append(link)
    return filtered


def extract_blog_landing(
    job_id: str,
    domain: str,
    url: str,
    html: str,
    parser: HtmlParser,
    rich_meta: dict,
    template: BaseTemplate | None = None,
    logger: Any | None = None,
) -> tuple[dict, list[str]]:
    """Blog landing page: extract article links. Ported from BlogExtractorWorker.

    When a platform template matched, its (more targeted) ``article_link``
    selectors are unioned with the generic selector list — additional
    platform-specific selectors can only add true-positive links here,
    they never remove links the generic selectors already find.
    """
    logger = logger or log
    article_links = parser.extract_links(
        selectors=[
            "article a", "h2 a", ".post-title a",
            ".entry-title a", "a[rel='bookmark']",
        ],
        base_url=url,
    )

    if template is not None:
        try:
            template_links = template.extract_blog_urls(html, url)
        except Exception as e:
            logger.warning(
                "template_extract_blog_urls_failed",
                url=url,
                template=template.config.id,
                error=str(e),
            )
            template_links = []
        if template_links:
            article_links = list(dict.fromkeys([*article_links, *template_links]))

    article_links = filter_article_links(article_links, url)

    metadata = BlogUrlMetadata(
        blog_landing_url=url,
        article_urls=article_links,
        total_articles=len(article_links),
    )
    record = {
        "job_id": UUID(job_id),
        "domain": domain,
        "data_type": DataType.BLOG_URL,
        "url": url,
        "title": parser.extract_title(),
        "metadata": {**rich_meta, **metadata.model_dump()},
    }
    return record, article_links


def extract_contacts(
    job_id: str, domain: str, url: str, html: str, rich_meta: dict,
) -> list[dict]:
    """Contact records from team/about pages. Ported from ContactFinderWorker."""
    cp = ContactParser(html, url)
    people = cp.extract_people()
    records = []
    for person in people:
        meta = ContactMetadata(**person)
        name = f"{meta.first_name or ''} {meta.last_name or ''}".strip() or None
        records.append({
            "job_id": UUID(job_id),
            "domain": domain,
            "data_type": DataType.CONTACT,
            "url": url,
            "title": name,
            "metadata": {**rich_meta, **meta.model_dump()},
        })
    return records


def extract_tech_stack(
    job_id: str,
    domain: str,
    url: str,
    html: str,
    headers: dict[str, str],
    rich_meta: dict,
) -> dict | None:
    """Tech stack for a page, via the precompiled catalog engine.

    Returns flat per-category lists plus a `detections` list carrying
    confidence, version and evidence for each match. Every field the engine
    folds into is copied across, so adding a category to the catalog does not
    require touching this function.
    """
    signals = extract_page_signals(html, url=url, headers=headers)
    detected = detections_to_metadata(detect_tech(signals))
    metadata = TechStackMetadata(**{
        k: v for k, v in detected.items() if k in TechStackMetadata.model_fields
    })
    return {
        "job_id": UUID(job_id),
        "domain": domain,
        "data_type": DataType.TECH_STACK,
        "url": url,
        "title": f"Tech Stack: {domain}",
        "metadata": {**rich_meta, **metadata.model_dump()},
    }


def extract_resources(
    job_id: str, domain: str, url: str, html: str, rich_meta: dict,
) -> list[dict]:
    """Resource records. Ported from ResourceFinderWorker."""
    rp = ResourceParser(html, url)
    resources = rp.extract_resources()
    records = []
    for resource in resources:
        meta = ResourceMetadata(**resource)
        records.append({
            "job_id": UUID(job_id),
            "domain": domain,
            "data_type": DataType.RESOURCE,
            "url": resource.get("url", url),
            "title": resource.get("title"),
            "metadata": {**rich_meta, **meta.model_dump()},
        })
    return records


def extract_pricing(
    job_id: str, domain: str, url: str, html: str, rich_meta: dict,
) -> list[dict]:
    """Pricing plan records. Ported from PricingFinderWorker."""
    pp = PricingParser(html, url)
    plans = pp.extract_pricing_plans()
    records = []
    for plan in plans:
        meta = PricingMetadata(**plan)
        records.append({
            "job_id": UUID(job_id),
            "domain": domain,
            "data_type": DataType.PRICING,
            "url": url,
            "title": plan.get("plan_name"),
            "metadata": {**rich_meta, **meta.model_dump()},
        })
    return records
