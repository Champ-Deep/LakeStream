"""Unified content worker: fetches each URL once, extracts all data types.

Replaces the 6 separate workers (BlogExtractor, ArticleParser, ContactFinder,
TechDetector, ResourceFinder, PricingFinder) with a single pass that:
1. Fetches each URL exactly once (with escalation + rate limiting)
2. Saves full page content for every page
3. Runs specialized extractors based on URL classification

This class is orchestration-only: it owns the URL processing loop, pagination
capping, and persistence timing. Cancellation polling and heartbeating are
delegated to ``JobLifecycle`` (src/workers/job_lifecycle.py); the actual
data-type extraction strategies live in ``src/workers/extractors.py`` and
``src/workers/pdf_handler.py``; dict-to-ScrapedData wrapping is delegated to
``src/workers/dto.py``.
"""

from urllib.parse import urlparse

import structlog

from src.models.scraped_data import DataType, ScrapedData
from src.scraping.parser.html_parser import HtmlParser, extract_rich_metadata
from src.templates.base import BaseTemplate
from src.templates.registry import detect_template, get_template_instance
from src.workers import extractors
from src.workers.base import BaseWorker
from src.workers.dto import build_scraped_data_list
from src.workers.job_lifecycle import JobLifecycle
from src.workers.pdf_handler import build_document_record

log = structlog.get_logger()

# Re-exported for backward compatibility with call sites/tests that imported
# these constants from this module.
MIN_ARTICLE_WORDS = extractors.MIN_ARTICLE_WORDS


class ContentWorker(BaseWorker):
    """Fetches each URL once and runs all applicable extractors.

    Template consulting
    --------------------
    When a platform template (WordPress/HubSpot/Webflow/Directory) is
    resolved for a page — either explicitly via ``self.template`` or
    auto-detected from the fetched HTML via
    ``src.templates.registry.detect_template`` — its per-platform CSS
    selectors are used to *improve specific fields* on top of the existing
    generic-parser records, rather than replacing them outright:

    - Blog link discovery (``_extract_blog_landing``): the template's
      ``extract_blog_urls`` (platform-specific ``article_link`` selectors)
      is unioned with the generic selector list, since more targeted
      selectors on a matching platform should only add true positives.
    - Article title/author (``_extract_article_record``): the template's
      ``extract_article`` dict can override ``title``/``author`` when it
      finds a non-empty value with its platform-specific selectors. The
      article ``content``/``word_count``/``categories`` always keep coming
      from the generic ``HtmlParser`` — templates only return a short
      (300-char) excerpt from a single content selector, so trusting them
      for full body content would be a quality regression, not an
      improvement.

    Deliberately NOT wired in (left on the generic parsers):

    - Contacts: every concrete template's ``extract_contacts`` returns
      ``[]`` (dead stub) — there is nothing to gain by calling it.
    - Tech stack / resources / pricing: ``BaseTemplate`` has no methods for
      these data types at all.
    - ``GenericTemplate``: never consulted — for domains with no specific
      platform match, behavior is unchanged from today (100% generic
      parsers), rather than routing through ``GenericTemplate``'s
      differently-shaped (and less complete) extraction methods.
    """

    def _resolve_template(self, html: str, url: str) -> BaseTemplate | None:
        """Resolve the platform template to consult for this page, if any.

        Returns ``None`` for the generic/no-match case so callers can cheaply
        skip all template-based logic and fall back to the existing
        generic-parser behavior unchanged.

        - If a template was passed explicitly to the worker (``self.template``,
          set from a resolved ``template_id`` in the job), use it — unless it
          is the generic template, which carries no extraction benefit here.
        - Otherwise, auto-detect from the fetched HTML. ``detect_template``
          always returns *something* (falling back to ``GenericTemplate``),
          so we translate that fallback to ``None`` too.
        """
        if self.template is not None:
            instance = get_template_instance(self.template.id)
            if instance is not None and instance.config.id != "generic":
                return instance

        detected = detect_template(html, url)
        if detected.config.id == "generic":
            return None
        return detected

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

        lifecycle = JobLifecycle(self._pool, self.job_id, logger=self.log)

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

            # Cooperative cancellation check — run on EVERY URL so a user-cancel
            # exits within one URL iteration instead of waiting up to 5.
            if await lifecycle.is_cancelled(urls_processed=urls_processed):
                return all_results

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
            await lifecycle.maybe_heartbeat(urls_processed)

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

                # Cooperative cancellation check — Phase 2 must honor cancels too.
                if await lifecycle.is_cancelled(urls_processed=urls_processed, phase="phase2"):
                    return all_results

                try:
                    records = await self._process_url(url, DataType.ARTICLE, data_types)
                    all_results.extend(records)
                except Exception as e:
                    self.log.error("article_process_error", url=url, error=str(e))

                # Heartbeat every 5 URLs in phase 2 as well
                urls_processed += 1
                await lifecycle.maybe_heartbeat(urls_processed)

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
        if extractors.is_error_page(title):
            self.log.debug("skipping_error_page", url=url, title=title)
            return []

        rich_meta = extract_rich_metadata(html, url)
        records: list[dict] = []
        template = self._resolve_template(html, url)

        # --- ALWAYS: full page content ---
        records.append(self._extract_page_record(url, parser, rich_meta))

        # raw_only mode: save page content only, skip all specialized extraction
        if self.raw_only:
            if records:
                await self.export_results(records)
            return build_scraped_data_list(records, self.job_id, self.domain, url)

        # --- Specialized extraction based on URL classification ---

        if data_type == DataType.BLOG_URL and "blog_url" in data_types:
            blog_rec, _ = self._extract_blog_landing(url, html, parser, rich_meta, template)
            if blog_rec:
                records.append(blog_rec)

        # Article extraction for any page with substantial content
        if "article" in data_types and parser.count_words() >= MIN_ARTICLE_WORDS:
            article_rec = self._extract_article_record(url, html, parser, rich_meta, template)
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

        return build_scraped_data_list(records, self.job_id, self.domain, url)

    # ------------------------------------------------------------------
    # Extractor delegation — thin wrappers over src/workers/extractors.py
    # ------------------------------------------------------------------
    # Kept as methods (rather than inlining calls to `extractors.*` at each
    # call site) so existing direct callers/tests of these names keep working
    # unchanged, and so `self.log`/`self.job_id`/`self.domain` don't need to
    # be threaded through every call site in `_process_url`.

    def _extract_page_record(
        self, url: str, parser: HtmlParser, rich_meta: dict,
    ) -> dict:
        return extractors.extract_page_record(self.job_id, self.domain, url, parser, rich_meta)

    def _extract_article_record(
        self,
        url: str,
        html: str,
        parser: HtmlParser,
        rich_meta: dict,
        template: BaseTemplate | None = None,
    ) -> dict | None:
        return extractors.extract_article_record(
            self.job_id, self.domain, url, html, parser, rich_meta, template, logger=self.log,
        )

    def _extract_blog_landing(
        self,
        url: str,
        html: str,
        parser: HtmlParser,
        rich_meta: dict,
        template: BaseTemplate | None = None,
    ) -> tuple[dict, list[str]]:
        return extractors.extract_blog_landing(
            self.job_id, self.domain, url, html, parser, rich_meta, template, logger=self.log,
        )

    def _extract_contacts(
        self, url: str, html: str, rich_meta: dict,
    ) -> list[dict]:
        return extractors.extract_contacts(self.job_id, self.domain, url, html, rich_meta)

    def _extract_tech_stack(
        self,
        url: str,
        html: str,
        headers: dict[str, str],
        rich_meta: dict,
    ) -> dict | None:
        return extractors.extract_tech_stack(
            self.job_id, self.domain, url, html, headers, rich_meta,
        )

    def _extract_resources(
        self, url: str, html: str, rich_meta: dict,
    ) -> list[dict]:
        return extractors.extract_resources(self.job_id, self.domain, url, html, rich_meta)

    def _extract_pricing(
        self, url: str, html: str, rich_meta: dict,
    ) -> list[dict]:
        return extractors.extract_pricing(self.job_id, self.domain, url, html, rich_meta)

    def _filter_article_links(self, links: list[str], source_url: str) -> list[str]:
        return extractors.filter_article_links(links, source_url)

    # ------------------------------------------------------------------
    # PDF handling
    # ------------------------------------------------------------------

    async def _process_pdf(
        self, url: str, fetch_result: object,
    ) -> list[ScrapedData]:
        """Extract content from a PDF document, persist it, and return its DTO."""
        content_bytes = getattr(fetch_result, "content_bytes", None)
        record = build_document_record(
            self.job_id, self.domain, url, content_bytes, logger=self.log,
        )
        if record is None:
            return []

        await self.export_results([record])

        return build_scraped_data_list([record], self.job_id, self.domain, url)
