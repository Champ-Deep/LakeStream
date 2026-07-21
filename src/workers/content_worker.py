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
    DetectedTech,
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

        # Enforce max pages limit to prevent runaway jobs
        from src.config.settings import get_settings
        max_pages = get_settings().max_scrape_pages_per_job
        if len(classified_urls) > max_pages:
            self.log.warning(
                "url_list_capped",
                original=len(classified_urls),
                capped_to=max_pages,
            )
            classified_urls = classified_urls[:max_pages]

        self.log.info(
            "content_worker_start",
            url_count=len(classified_urls),
            data_types=data_types,
        )

        all_results: list[ScrapedData] = []
        fetched_urls: set[str] = set()
        article_urls_from_blogs: list[str] = []
        urls_processed = 0

        # --- Phase 1: Process all classified URLs ---
        for entry in classified_urls:
            url = entry["url"]
            data_type = entry.get("data_type", DataType.PAGE)

            if url in fetched_urls:
                continue
            fetched_urls.add(url)

            # Cooperative cancellation check
            if urls_processed % 5 == 0 and self._pool:
                try:
                    from src.db.queries.jobs import is_job_cancelled
                    if await is_job_cancelled(self._pool, UUID(self.job_id)):
                        self.log.info("job_cancelled_by_user", urls_processed=urls_processed)
                        return all_results
                except Exception:
                    pass

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

            # Heartbeat every 5 URLs to signal the job is still active
            urls_processed += 1
            if urls_processed % 5 == 0 and self._pool:
                try:
                    from src.db.queries.jobs import update_heartbeat
                    await update_heartbeat(self._pool, UUID(self.job_id))
                except Exception:
                    pass  # Non-critical — don't fail job over heartbeat

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

                # Heartbeat every 5 URLs in phase 2 as well
                urls_processed += 1
                if urls_processed % 5 == 0 and self._pool:
                    try:
                        from src.db.queries.jobs import update_heartbeat
                        await update_heartbeat(self._pool, UUID(self.job_id))
                    except Exception:
                        pass

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

        # Save a screenshot to storage if one was captured (Phase 3).
        screenshot_path = None
        if fetch_result.screenshot_bytes:
            from src.services.storage import get_storage

            screenshot_path = get_storage().save_screenshot(
                self.job_id, fetch_result.screenshot_bytes
            )

        # Persist durable page content (markdown + raw HTML + hash), record any
        # change, and learn whether the page is unchanged since last fetch
        # (v2, flag-gated).
        cache_hit = await self._handle_page_content(url, html, parser, screenshot_path)

        # raw_only, or cache hit (content unchanged): save the page record only
        # and skip the expensive specialized/LLM/custom-schema extraction.
        if self.raw_only or cache_hit:
            if cache_hit and not self.raw_only:
                self.log.info("cache_hit_skip_extraction", url=url)
            if records:
                await self.export_results(records)
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

        # --- LLM modes: off | fallback | only ---
        llm_mode = getattr(self, "llm_mode", "off")

        # CSS extraction runs unless llm_mode == "only"
        run_css = llm_mode != "only"
        # LLM extraction runs when fallback or only
        run_llm = llm_mode in ("fallback", "only")

        if run_css:
            # --- Specialized CSS extraction based on URL classification ---

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

            # Tech stack: scan every page, merge signals across the site
            if "tech_stack" in data_types:
                tech_rec = self._extract_tech_stack(
                    url, html, fetch_result.headers, rich_meta,
                )
                if tech_rec:
                    records.append(tech_rec)

        # --- LLM extraction: runs on every page for every requested type ---
        if run_llm:
            llm_records = await self._llm_extract_every_type(url, html, data_types)
            if llm_records:
                records.extend(llm_records)

        # --- Custom-schema extraction (markdown + schema fallback) ---
        # When a job supplies a custom schema, run it on every page. Pages that
        # match no built-in typed schema still yield structured data here (or,
        # failing that, the persisted markdown above), so nothing is lost.
        if self.extraction_schema:
            ext_rec = await self._extract_custom_schema(url, html)
            if ext_rec:
                records.append(ext_rec)

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

    async def _handle_page_content(
        self, url: str, html: str, parser: HtmlParser, screenshot_path: str | None = None,
    ) -> bool:
        """Persist page content, record changes, and report whether it's a cache hit.

        Returns True when the content is unchanged since the last fetch and the
        cache is enabled — callers may then skip expensive re-extraction.
        """
        from src.config.settings import get_settings

        settings = get_settings()
        if not settings.enable_content_persistence or not self._pool:
            return False
        try:
            from src.db.queries.page_content import get_hash, upsert_page_content
            from src.scraping.parser.markdown import content_hash, html_to_markdown

            user_uuid = UUID(self.user_id) if self.user_id else None
            markdown = html_to_markdown(html, find_main=True)
            new_hash = content_hash(markdown)
            old_hash = await get_hash(self._pool, url, user_uuid)

            if old_hash is not None and old_hash != new_hash and settings.enable_change_monitoring:
                await self._record_change(url, old_hash, new_hash, user_uuid)

            await upsert_page_content(
                self._pool,
                domain=self.domain,
                url=url,
                content_hash=new_hash,
                markdown=markdown,
                raw_html=html,
                title=parser.extract_title(),
                screenshot_path=screenshot_path,
                org_id=UUID(self.org_id) if self.org_id else None,
                user_id=user_uuid,
            )

            return (
                settings.enable_scrape_cache
                and not self.force_refresh
                and old_hash is not None
                and old_hash == new_hash
            )
        except Exception as e:
            self.log.warning("page_content_handle_failed", url=url, error=str(e))
            return False

    async def _record_change(
        self, url: str, old_hash: str, new_hash: str, user_uuid: UUID | None,
    ) -> None:
        """Log a content change and fire the tracked-domain webhook, if any."""
        try:
            from src.db.queries.content_changes import insert_change

            await insert_change(
                self._pool, domain=self.domain, url=url,
                old_hash=old_hash, new_hash=new_hash, user_id=user_uuid,
            )
            from src.db.queries.tracked_domains import get_tracked_domain

            tracked = await get_tracked_domain(self._pool, self.domain)
            if tracked and getattr(tracked, "webhook_url", None):
                from src.services.change_monitor import notify_content_change

                await notify_content_change(
                    tracked.webhook_url, domain=self.domain, url=url,
                    old_hash=old_hash, new_hash=new_hash,
                )
        except Exception as e:
            self.log.warning("record_change_failed", url=url, error=str(e))

    async def _extract_custom_schema(self, url: str, html: str) -> dict | None:
        """Run the job's custom extraction schema (css/ai/auto) on one page."""
        from src.models.extraction import ExtractionSchema
        from src.services.structured_extract import AIUnavailableError, extract_with_fallback

        try:
            schema = ExtractionSchema(**self.extraction_schema)
        except Exception as e:
            self.log.warning("invalid_extraction_schema", error=str(e))
            return None

        try:
            result = await extract_with_fallback(
                html, url, schema, self.extraction_mode, org_id=self.org_id,
            )
        except AIUnavailableError:
            self.log.info("custom_schema_ai_unavailable", url=url)
            return None

        if not result or not result.data:
            return None

        return {
            "job_id": UUID(self.job_id),
            "domain": self.domain,
            "data_type": DataType.EXTRACTED,
            "url": url,
            "title": None,
            "metadata": {"schema": schema.name, "mode": result.mode, "fields": result.data},
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
        """Detect tech stack from raw HTML source code + HTTP headers.
        Returns a record with flat lists (backward compat) and an enriched
        detections list carrying confidence + evidence per match."""
        tp = TechParser(html, headers)
        detected = tp.detect()
        metadata = TechStackMetadata(
            platform=detected.get("platform"),
            frameworks=detected.get("frameworks", []),
            js_libraries=detected.get("js_libraries", []),
            analytics=detected.get("analytics", []),
            marketing_tools=detected.get("marketing_tools", []),
            cdn=detected.get("cdn", []),
            hosting=detected.get("hosting", []),
            backend=detected.get("backend", []),
            build_tools=detected.get("build_tools", []),
            fonts=detected.get("fonts", []),
            payment=detected.get("payment", []),
            auth=detected.get("auth", []),
            monitoring=detected.get("monitoring", []),
            search=detected.get("search", []),
            ecommerce=detected.get("ecommerce", []),
            detections=[DetectedTech(**d) for d in detected.get("detections", [])],
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

    # ------------------------------------------------------------------
    # LLM extraction (runs on every page when llm_mode is fallback or only)
    # ------------------------------------------------------------------

    async def _llm_extract_every_type(
        self,
        url: str,
        html: str,
        data_types: list[str],
    ) -> list[dict]:
        """Run LLM extraction on this page for every requested data type.

        Called when llm_mode is 'fallback' or 'only'. Unlike CSS extraction
        which is gated by URL classification, this runs for every requested
        data type on every page — maximizing extraction coverage.
        """
        from src.services.llm_extractor import LLMExtractor

        llm = LLMExtractor(org_id=self.org_id)
        results: list[dict] = []

        # Run LLM extraction per requested data type
        # Skip 'page' (already saved as raw), tech_stack is homepage-only
        for dt in data_types:
            # tech_stack: only run on homepage to avoid waste
            if dt == "tech_stack":
                path = urlparse(url).path.rstrip("/")
                if path not in ("", "/index.html"):
                    continue

            try:
                llm_data = await llm.extract_by_type(html, dt)
                if not llm_data or "_extraction_errors" in llm_data:
                    self.log.debug("llm_no_results", url=url, data_type=dt)
                    continue

                converted = self._convert_llm_results(url, dt, llm_data)
                if converted:
                    results.extend(converted)
                    self.log.info(
                        "llm_extracted",
                        url=url,
                        data_type=dt,
                        records=len(converted),
                    )
            except Exception as e:
                # Non-fatal — log and continue with other types
                self.log.warning(
                    "llm_extract_failed",
                    url=url,
                    data_type=dt,
                    error=str(e),
                )

        return results

    def _convert_llm_results(self, url: str, data_type: str, llm_data: dict) -> list[dict]:
        """Convert LLM output to record dicts matching CSS extractor format."""
        records: list[dict] = []

        if data_type == "contact":
            people = llm_data.get("people", [])
            if isinstance(people, list):
                for person in people:
                    if not isinstance(person, dict):
                        continue
                    # Skip empty entries
                    if not any(person.get(k) for k in ("first_name", "last_name", "email", "job_title")):
                        continue
                    name = f"{person.get('first_name', '')} {person.get('last_name', '')}".strip()
                    records.append({
                        "job_id": UUID(self.job_id),
                        "domain": self.domain,
                        "data_type": DataType.CONTACT,
                        "url": url,
                        "title": name or None,
                        "metadata": {**person, "extraction_method": "llm"},
                    })

        elif data_type == "pricing":
            plans = llm_data.get("plans", [])
            if isinstance(plans, list):
                for plan in plans:
                    if not isinstance(plan, dict) or not plan.get("plan_name"):
                        continue
                    records.append({
                        "job_id": UUID(self.job_id),
                        "domain": self.domain,
                        "data_type": DataType.PRICING,
                        "url": url,
                        "title": plan.get("plan_name"),
                        "metadata": {**plan, "extraction_method": "llm"},
                    })

        elif data_type == "article":
            # Only add if LLM found substantive content
            if llm_data.get("content") or llm_data.get("excerpt"):
                records.append({
                    "job_id": UUID(self.job_id),
                    "domain": self.domain,
                    "data_type": DataType.ARTICLE,
                    "url": url,
                    "title": llm_data.get("title") or llm_data.get("author"),
                    "metadata": {**llm_data, "extraction_method": "llm"},
                })

        elif data_type == "tech_stack":
            # Only add if LLM found at least one tech identifier
            has_tech = any(llm_data.get(k) for k in ("platform", "js_libraries", "frameworks", "analytics", "marketing_tools"))
            if has_tech:
                records.append({
                    "job_id": UUID(self.job_id),
                    "domain": self.domain,
                    "data_type": DataType.TECH_STACK,
                    "url": url,
                    "title": f"Tech Stack: {self.domain}",
                    "metadata": {**llm_data, "extraction_method": "llm"},
                })

        elif data_type == "resource":
            resources = llm_data.get("resources", [])
            if isinstance(resources, list):
                for res in resources:
                    if not isinstance(res, dict) or not res.get("title"):
                        continue
                    records.append({
                        "job_id": UUID(self.job_id),
                        "domain": self.domain,
                        "data_type": DataType.RESOURCE,
                        "url": res.get("download_url") or url,
                        "title": res.get("title"),
                        "metadata": {**res, "extraction_method": "llm"},
                    })

        elif data_type == "blog_url":
            articles = llm_data.get("articles", [])
            if isinstance(articles, list) and articles:
                records.append({
                    "job_id": UUID(self.job_id),
                    "domain": self.domain,
                    "data_type": DataType.BLOG_URL,
                    "url": url,
                    "title": f"Blog Index: {url}",
                    "metadata": {
                        "blog_landing_url": url,
                        "article_urls": [a.get("url") for a in articles if isinstance(a, dict) and a.get("url")],
                        "total_articles": len(articles),
                        "extraction_method": "llm",
                    },
                })

        return records
