import structlog

from src.scraping.parser.url_classifier import classify_urls
from src.scraping.validator.url_validator import validate_and_deduplicate
from src.services.crawler import CrawlerService
from src.utils.url import ensure_scheme

log = structlog.get_logger()


class DomainMapperWorker:
    """Discovers all URLs for a domain using CrawlerService, then classifies them."""

    def __init__(self, domain: str, job_id: str, org_id: str | None = None, pool=None):
        self.domain = domain
        self.job_id = job_id
        self.org_id = org_id
        self.pool = pool
        self.crawler = CrawlerService(
            max_concurrent=15, max_per_domain=6, pool=pool, job_id=job_id,
        )
        self.log = log.bind(worker="DomainMapper", domain=domain, job_id=job_id)

    async def _heartbeat(self) -> None:
        """Update heartbeat so stale-job recovery doesn't kill us during crawl."""
        if self.pool is None:
            return
        try:
            from uuid import UUID
            from src.db.queries.jobs import update_heartbeat
            await update_heartbeat(self.pool, UUID(self.job_id))
        except Exception as e:
            self.log.warning("heartbeat_failed", error=str(e))

    async def execute(self, max_pages: int | None = None) -> list[dict]:
        """Map a domain and return classified URLs.

        Args:
            max_pages: Maximum pages to crawl (None for unlimited)

        Returns:
            List of classified URLs with data_type annotations
        """
        self.log.info("mapping_domain", max_pages=max_pages or "unlimited")

        # Signal the job is still alive before starting the potentially long crawl
        await self._heartbeat()

        # 1. Discover URLs via CrawlerService (unlimited by default)
        url = ensure_scheme(self.domain)
        raw_urls = await self.crawler.map_domain(url, limit=max_pages)

        # Heartbeat after crawl completes (may have taken minutes)
        await self._heartbeat()
        self.log.info("urls_discovered", count=len(raw_urls))

        # 2. Validate and deduplicate (keep duplicates from traversal if max_pages is None)
        valid_urls = validate_and_deduplicate(raw_urls)
        self.log.info("urls_validated", count=len(valid_urls))

        # 3. Classify by data type
        classified = classify_urls(valid_urls)
        self.log.info(
            "urls_classified",
            total=len(classified),
            types={
                dt: len([c for c in classified if c["data_type"] == dt])
                for dt in set(c["data_type"] for c in classified)
            },
        )

        return classified
