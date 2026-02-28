from abc import ABC, abstractmethod
from urllib.parse import urlparse

import structlog

from src.models.scraped_data import ScrapedData
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier
from src.models.template import TemplateConfig
from src.scraping.fetcher.factory import create_fetcher
from src.services.cost_tracker import CostTracker
from src.services.rate_limiter import RateLimiter
from src.utils.retry import retry_async


class BaseWorker(ABC):
    def __init__(
        self,
        domain: str,
        job_id: str,
        template: TemplateConfig | None = None,
        pool: object | None = None,
    ):
        self.domain = domain
        self.job_id = job_id
        self.template = template
        self._pool = pool
        self.log = structlog.get_logger().bind(
            worker=self.__class__.__name__, domain=domain, job_id=job_id
        )
        self._cost_tracker = CostTracker()
        self._rate_limiter = RateLimiter()

        if pool is not None:
            from src.services.escalation import EscalationService

            self._escalation = EscalationService(pool)
        else:
            self._escalation = None  # type: ignore[assignment]

    @abstractmethod
    async def execute(self, urls: list[str]) -> list[ScrapedData]: ...

    async def fetch_page(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        """Fetch a page with automatic tier escalation, rate limiting, and cost tracking."""
        domain = urlparse(url).netloc or self.domain

        if self._escalation is None:
            await self._rate_limiter.wait(domain)
            fetcher = create_fetcher(ScrapingTier.BASIC_HTTP)
            result = await retry_async(
                fetcher.fetch,
                url,
                options,
                max_retries=2,
                base_delay=2.0,
                retry_on=(ConnectionError, TimeoutError, OSError),
            )
            self._rate_limiter.report_result(domain, result.status_code)
            self._cost_tracker.record_cost(self.job_id, self.domain, result.tier_used.value)
            return result

        current_tier = await self._escalation.decide_initial_tier(self.domain)

        while True:
            await self._rate_limiter.wait(domain)
            fetcher = create_fetcher(current_tier)
            result = await retry_async(
                fetcher.fetch,
                url,
                options,
                max_retries=2,
                base_delay=2.0,
                retry_on=(ConnectionError, TimeoutError, OSError),
            )
            self._rate_limiter.report_result(domain, result.status_code)
            self._cost_tracker.record_cost(self.job_id, self.domain, result.tier_used.value)

            if not self._escalation.should_escalate(result):
                await self._escalation.record_result(self.domain, result, success=True)
                return result

            if not self._cost_tracker.check_budget(self.job_id):
                await self._escalation.record_result(self.domain, result, success=False)
                return result

            next_tier = self._escalation.get_next_tier(current_tier)
            if next_tier is None:
                await self._escalation.record_result(self.domain, result, success=False)
                return result

            self.log.info(
                "fetch_escalating", url=url, from_tier=current_tier.value, to_tier=next_tier.value
            )
            current_tier = next_tier

    async def export_results(self, data: list[dict]) -> int:
        from src.db.pool import get_pool
        from src.db.queries.scraped_data import batch_insert_scraped_data

        pool = self._pool or await get_pool()
        return await batch_insert_scraped_data(pool, data)  # type: ignore[arg-type]
