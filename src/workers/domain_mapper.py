import structlog

from src.scraping.parser.url_classifier import classify_urls
from src.scraping.validator.url_validator import validate_and_deduplicate
from src.services.crawler import CrawlerService
from src.utils.url import ensure_scheme

log = structlog.get_logger()


class DomainMapperWorker:
    """Discovers all URLs for a domain using CrawlerService, then classifies them."""

    def __init__(self, domain: str, job_id: str, org_id: str | None = None, tier_override: str | None = None):
        self.domain = domain
        self.job_id = job_id
        self.org_id = org_id
        self.tier_override = tier_override
        self.crawler = CrawlerService()
        self.log = log.bind(worker="DomainMapper", domain=domain, job_id=job_id)

    async def execute(self, max_pages: int = 500) -> list[dict]:
        """Map a domain and return classified URLs."""
        self.log.info("mapping_domain", max_pages=max_pages)

        # 1. Discover URLs via CrawlerService
        url = ensure_scheme(self.domain)
        raw_urls = await self.crawler.map_domain(url, limit=max_pages)
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

        return classified
