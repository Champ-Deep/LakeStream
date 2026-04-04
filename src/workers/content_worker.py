"""Unified content worker: fetches each URL once, extracts all data types.

Replaces the 6 separate workers (BlogExtractor, ArticleParser, ContactFinder,
TechDetector, ResourceFinder, PricingFinder) with a single pass that:
1. Fetches each URL exactly once (with escalation + rate limiting)
2. Saves full page content for every page
3. Runs specialized extractors based on URL classification
"""

from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

import structlog

from src.models.scraped_data import (
    ArticleMetadata,
    BlogUrlMetadata,
    ContactMetadata,
    DataType,
    DocumentMetadata,
    PricingMetadata,
    ResourceMetadata,
    ScrapedData,
    TechStackMetadata,
)
from src.scraping.parser.contact_parser import ContactParser
from src.scraping.parser.html_parser import HtmlParser, extract_rich_metadata
from src.scraping.parser.pricing_parser import PricingParser
from src.scraping.parser.resource_parser import ResourceParser
from src.scraping.parser.tech_parser import TechParser
from src.utils.url import extract_domain
from src.workers.base import BaseWorker

log = structlog.get_logger()

# Minimum word count to treat a page as having article-worthy content
MIN_ARTICLE_WORDS = 200

_SKIP_EXTENSIONS = frozenset({
    ".doc", ".docx", ".zip", ".png", ".jpg", ".jpeg",
    ".gif", ".svg", ".webp", ".mp3", ".mp4", ".avi",
})
# PDF is handled separately — not skipped

_ERROR_MARKERS = ("error", "404", "not found", "page not found")


class ContentWorker(BaseWorker):
    """Fetches each URL once and runs all applicable extractors."""

    async def execute(  # type: ignore[override]
        self,
        classified_urls: list[dict],
        data_types: list[str],
    ) -> list[ScrapedData]:
        """Process all classified URLs and extract all requested data types.

        Phase 1: Fetch + extract every classified URL.
        Phase 2: Follow up article URLs discovered from blog landing pages.
        """
        if not classified_urls:
            self.log.info("no_urls_to_process")
            return []

        self.log.info(
            "content_worker_start",
            url_count=len(classified_urls),
            data_types=data_types,
        )

        all_results: list[ScrapedData] = []
        fetched_urls: set[str] = set()
        article_urls_from_blogs: list[str] = []

        # --- Phase 1: Process all classified URLs ---
        for entry in classified_urls:
            url = entry["url"]
            data_type = entry.get("data_type", DataType.PAGE)

            if url in fetched_urls:
                continue
            fetched_urls.add(url)

            try:
                records = await self._process_url(url, data_type, data_types)

                # Collect article URLs discovered from blog landing pages
                for r in records:
                    if r.data_type == DataType.BLOG_URL and isinstance(r.metadata, dict):
                        discovered = r.metadata.get("article_urls", [])
                        article_urls_from_blogs.extend(discovered)

                all_results.extend(records)
            except Exception as e:
                self.log.error("process_url_error", url=url, error=str(e))

        # --- Phase 2: Fetch article URLs discovered from blog landing pages ---
        if "article" in data_types and article_urls_from_blogs:
            self.log.info(
                "processing_blog_articles",
                count=len(article_urls_from_blogs),
            )
            for url in article_urls_from_blogs:
                if url in fetched_urls:
                    continue
                fetched_urls.add(url)
                try:
                    records = await self._process_url(url, DataType.ARTICLE, data_types)
                    all_results.extend(records)
                except Exception as e:
                    self.log.error("article_process_error", url=url, error=str(e))

        self.log.info("content_worker_done", total_records=len(all_results))
        return all_results

    # ------------------------------------------------------------------
    # Core: fetch once, extract everything
    # ------------------------------------------------------------------

    async def _process_url(
        self,
        url: str,
        data_type: str,
        data_types: list[str],
    ) -> list[ScrapedData]:
        """Fetch a single URL and run all applicable extractors."""
        fetch_result = await self.fetch_page(url)
        if fetch_result.blocked:
            self.log.warning("blocked", url=url, status=fetch_result.status_code)
            return []

        # --- PDF handling ---
        if fetch_result.content_type and "pdf" in fetch_result.content_type:
            return await self._process_pdf(url, fetch_result)

        html = fetch_result.html
        if not html or len(html) < 100:
            return []

        parser = HtmlParser(html, url)
        title = parser.extract_title()

        # Skip error pages
        if title and any(m in title.lower() for m in _ERROR_MARKERS):
            self.log.debug("skipping_error_page", url=url, title=title)
            return []

        rich_meta = extract_rich_metadata(html, url)
        records: list[dict] = []

        # --- ALWAYS: full page content ---
        records.append(self._extract_page_record(url, parser, rich_meta))

        # --- Specialized extraction based on URL classification ---

        if data_type == DataType.BLOG_URL and "blog_url" in data_types:
            blog_rec, _ = self._extract_blog_landing(url, html, parser, rich_meta)
            if blog_rec:
                records.append(blog_rec)

        # Article extraction for any page with substantial content
        if "article" in data_types and parser.count_words() >= MIN_ARTICLE_WORDS:
            article_rec = self._extract_article_record(url, parser, rich_meta)
            if article_rec:
                records.append(article_rec)

        if data_type == DataType.CONTACT and "contact" in data_types:
            records.extend(self._extract_contacts(url, html, rich_meta))

        if data_type == DataType.RESOURCE and "resource" in data_types:
            records.extend(self._extract_resources(url, html, rich_meta))

        if data_type == DataType.PRICING and "pricing" in data_types:
            records.extend(self._extract_pricing(url, html, rich_meta))

        # Tech stack: homepage only
        if "tech_stack" in data_types:
            path = urlparse(url).path.rstrip("/")
            if path in ("", "/index.html"):
                tech_rec = self._extract_tech_stack(
                    url, html, fetch_result.headers, rich_meta,
                )
                if tech_rec:
                    records.append(tech_rec)

        # Batch insert all records for this URL
        if records:
            await self.export_results(records)

        # Convert to ScrapedData for return value
        return [
            ScrapedData(
                id=UUID(int=0),
                job_id=UUID(self.job_id),
                domain=self.domain,
                data_type=rec["data_type"],
                url=rec.get("url", url),
                title=rec.get("title"),
                metadata=rec.get("metadata", {}),
                scraped_at=datetime.now(UTC),
            )
            for rec in records
        ]

    # ------------------------------------------------------------------
    # Extractors — ported 1:1 from existing workers
    # ------------------------------------------------------------------

    def _extract_page_record(
        self, url: str, parser: HtmlParser, rich_meta: dict,
    ) -> dict:
        """Full page content record — saved for every successfully fetched page."""
        content = parser.extract_content()
        word_count = len(content.split()) if content else 0
        return {
            "job_id": UUID(self.job_id),
            "domain": self.domain,
            "data_type": DataType.PAGE,
            "url": url,
            "title": parser.extract_title(),
            "metadata": {**rich_meta, "content": content, "word_count": word_count},
        }

    def _extract_article_record(
        self, url: str, parser: HtmlParser, rich_meta: dict,
    ) -> dict | None:
        """Article record for pages with substantial text. Ported from ArticleParserWorker."""
        content = parser.extract_content()
        word_count = parser.count_words()
        excerpt = parser.extract_meta("description")

        if word_count == 0 and excerpt is None:
            return None

        metadata = ArticleMetadata(
            author=parser.extract_meta("author"),
            categories=parser.extract_categories(),
            word_count=word_count,
            excerpt=excerpt,
            content=content,
        )
        return {
            "job_id": UUID(self.job_id),
            "domain": self.domain,
            "data_type": DataType.ARTICLE,
            "url": url,
            "title": parser.extract_title(),
            "metadata": {**rich_meta, **metadata.model_dump()},
        }

    def _extract_blog_landing(
        self, url: str, html: str, parser: HtmlParser, rich_meta: dict,
    ) -> tuple[dict, list[str]]:
        """Blog landing page: extract article links. Ported from BlogExtractorWorker."""
        article_links = parser.extract_links(
            selectors=[
                "article a", "h2 a", ".post-title a",
                ".entry-title a", "a[rel='bookmark']",
            ],
            base_url=url,
        )
        article_links = self._filter_article_links(article_links, url)

        metadata = BlogUrlMetadata(
            blog_landing_url=url,
            article_urls=article_links,
            total_articles=len(article_links),
        )
        record = {
            "job_id": UUID(self.job_id),
            "domain": self.domain,
            "data_type": DataType.BLOG_URL,
            "url": url,
            "title": parser.extract_title(),
            "metadata": {**rich_meta, **metadata.model_dump()},
        }
        return record, article_links

    def _extract_contacts(
        self, url: str, html: str, rich_meta: dict,
    ) -> list[dict]:
        """Contact records from team/about pages. Ported from ContactFinderWorker."""
        cp = ContactParser(html, url)
        people = cp.extract_people()
        records = []
        for person in people:
            meta = ContactMetadata(**person)
            name = f"{meta.first_name or ''} {meta.last_name or ''}".strip() or None
            records.append({
                "job_id": UUID(self.job_id),
                "domain": self.domain,
                "data_type": DataType.CONTACT,
                "url": url,
                "title": name,
                "metadata": {**rich_meta, **meta.model_dump()},
            })
        return records

    def _extract_tech_stack(
        self,
        url: str,
        html: str,
        headers: dict[str, str],
        rich_meta: dict,
    ) -> dict | None:
        """Tech stack from homepage. Ported from TechDetectorWorker."""
        tp = TechParser(html, headers)
        detected = tp.detect()
        metadata = TechStackMetadata(
            platform=detected.get("platform"),
            js_libraries=detected.get("js_libraries", []),
            analytics=detected.get("analytics", []),
            marketing_tools=detected.get("marketing_tools", []),
            frameworks=detected.get("frameworks", []),
        )
        return {
            "job_id": UUID(self.job_id),
            "domain": self.domain,
            "data_type": DataType.TECH_STACK,
            "url": url,
            "title": f"Tech Stack: {self.domain}",
            "metadata": {**rich_meta, **metadata.model_dump()},
        }

    def _extract_resources(
        self, url: str, html: str, rich_meta: dict,
    ) -> list[dict]:
        """Resource records. Ported from ResourceFinderWorker."""
        rp = ResourceParser(html, url)
        resources = rp.extract_resources()
        records = []
        for resource in resources:
            meta = ResourceMetadata(**resource)
            records.append({
                "job_id": UUID(self.job_id),
                "domain": self.domain,
                "data_type": DataType.RESOURCE,
                "url": resource.get("url", url),
                "title": resource.get("title"),
                "metadata": {**rich_meta, **meta.model_dump()},
            })
        return records

    def _extract_pricing(
        self, url: str, html: str, rich_meta: dict,
    ) -> list[dict]:
        """Pricing plan records. Ported from PricingFinderWorker."""
        pp = PricingParser(html, url)
        plans = pp.extract_pricing_plans()
        records = []
        for plan in plans:
            meta = PricingMetadata(**plan)
            records.append({
                "job_id": UUID(self.job_id),
                "domain": self.domain,
                "data_type": DataType.PRICING,
                "url": url,
                "title": plan.get("plan_name"),
                "metadata": {**rich_meta, **meta.model_dump()},
            })
        return records

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _filter_article_links(self, links: list[str], source_url: str) -> list[str]:
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

    async def _process_pdf(
        self, url: str, fetch_result: object,
    ) -> list[ScrapedData]:
        """Extract content from a PDF document."""
        from src.scraping.parser.pdf_parser import parse_pdf, pdf_to_markdown

        content_bytes = getattr(fetch_result, "content_bytes", None)
        if not content_bytes:
            self.log.warning("pdf_no_content", url=url)
            return []

        try:
            result = parse_pdf(content_bytes)
        except ValueError as e:
            self.log.warning("pdf_parse_error", url=url, error=str(e))
            return []

        if not result.text and not result.tables:
            return []

        markdown = pdf_to_markdown(result)

        metadata = DocumentMetadata(
            source_type="pdf",
            page_count=result.page_count,
            author=result.metadata.get("author"),
            tables=result.tables,
            word_count=result.word_count,
            text_content=markdown,
        )

        record = {
            "job_id": UUID(self.job_id),
            "domain": self.domain,
            "data_type": DataType.DOCUMENT,
            "url": url,
            "title": result.metadata.get("title") or f"PDF: {url.split('/')[-1]}",
            "metadata": metadata.model_dump(),
        }

        await self.export_results([record])

        return [
            ScrapedData(
                id=UUID(int=0),
                job_id=UUID(self.job_id),
                domain=self.domain,
                data_type=DataType.DOCUMENT,
                url=url,
                title=record["title"],
                metadata=record["metadata"],
                scraped_at=datetime.now(UTC),
            )
        ]
