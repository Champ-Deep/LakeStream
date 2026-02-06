import structlog

from src.scraping.parser.url_classifier import classify_urls
from src.scraping.validator.url_validator import validate_and_deduplicate
from src.services.firecrawl import FirecrawlService
from src.utils.url import ensure_scheme

log = structlog.get_logger()


class DomainMapperWorker:
    """Discovers all URLs for a domain using Firecrawl map, then classifies them."""

    def __init__(self, domain: str, job_id: str):
        self.domain = domain
        self.job_id = job_id
        self.firecrawl = FirecrawlService()
        self.log = log.bind(worker="DomainMapper", domain=domain, job_id=job_id)

    async def execute(self, max_pages: int = 100) -> list[dict]:
        """Map a domain and return classified URLs."""
        self.log.info("mapping_domain", max_pages=max_pages)

        # 1. Discover URLs via Firecrawl
        url = ensure_scheme(self.domain)
        raw_urls = await self.firecrawl.map_domain(url, limit=max_pages)
        self.log.info("urls_discovered", count=len(raw_urls))

        # 2. Validate and deduplicate
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

        # 4. Store in database
        from uuid import UUID

        from src.db.pool import get_pool
        from src.db.queries.scraped_data import batch_insert_scraped_data

        pool = await get_pool()
        records = [
            {
                "job_id": UUID(self.job_id),
                "domain": self.domain,
                "data_type": c["data_type"],
                "url": c["url"],
                "title": None,
                "metadata": {"confidence": c.get("confidence", 0)},
            }
            for c in classified
        ]
        if records:
            await batch_insert_scraped_data(pool, records)

        return classified
