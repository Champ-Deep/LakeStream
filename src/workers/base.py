from abc import ABC, abstractmethod

import structlog

from src.models.scraped_data import ScrapedData
from src.models.scraping import FetchOptions, FetchResult
from src.models.template import TemplateConfig


class BaseWorker(ABC):
    def __init__(
        self,
        domain: str,
        job_id: str,
        template: TemplateConfig | None = None,
    ):
        self.domain = domain
        self.job_id = job_id
        self.template = template
        self.log = structlog.get_logger().bind(
            worker=self.__class__.__name__, domain=domain, job_id=job_id
        )

    @abstractmethod
    async def execute(self, urls: list[str]) -> list[ScrapedData]: ...

    async def fetch_page(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        """Fetch a page. Wired to escalation service in B.7."""
        from src.models.scraping import ScrapingTier
        from src.scraping.fetcher.factory import create_fetcher

        fetcher = create_fetcher(ScrapingTier.BASIC_HTTP)
        return await fetcher.fetch(url, options)

    async def export_results(self, data: list[dict]) -> int:
        """Export results to Postgres."""
        from src.db.pool import get_pool
        from src.db.queries.scraped_data import batch_insert_scraped_data

        pool = await get_pool()
        return await batch_insert_scraped_data(pool, data)
