from abc import ABC, abstractmethod
from urllib.parse import urlparse

import structlog

from src.models.scraped_data import ScrapedData
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier
from src.models.template import TemplateConfig
from src.scraping.fetcher.factory import create_fetcher
from src.services.rate_limiter import RateLimiter
from src.utils.retry import retry_async


class BaseWorker(ABC):
    def __init__(
        self,
        domain: str,
        job_id: str,
        template: TemplateConfig | None = None,
        pool: object | None = None,
        org_id: str | None = None,
        user_id: str | None = None,
        tier_override: str | None = None,
    ):
        self.domain = domain
        self.job_id = job_id
        self.template = template
        self._pool = pool
        self.org_id = org_id
        self.user_id = user_id
        self._tier_override = ScrapingTier(tier_override) if tier_override else None
        self.log = structlog.get_logger().bind(
            worker=self.__class__.__name__, domain=domain, job_id=job_id
        )
        self._rate_limiter = RateLimiter()

        if pool is not None:
            from src.services.escalation import EscalationService

            self._escalation = EscalationService(pool)
        else:
            self._escalation = None  # type: ignore[assignment]

    @abstractmethod
    async def execute(self, urls: list[str]) -> list[ScrapedData]: ...

    async def fetch_page(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        """Fetch a page with automatic tier escalation, rate limiting, and cost tracking.

        If tier_override is set, uses that tier directly (no escalation).
        """
        domain = urlparse(url).netloc or self.domain

        # If tier override is set, use it directly (no escalation)
        if self._tier_override:
            await self._rate_limiter.wait(domain)
            fetcher = create_fetcher(self._tier_override)
            result = await retry_async(
                fetcher.fetch,
                url,
                options,
                max_retries=2,
                base_delay=2.0,
                retry_on=(ConnectionError, TimeoutError, OSError),
            )
            self._rate_limiter.report_result(domain, result.status_code)
            self.log.info(
                "fetch_tier_override",
                url=url,
                tier=self._tier_override.value,
                status=result.status_code,
            )
            return result

        if self._escalation is None:
            # No DB pool — attempt lightpanda first, fall back through the tier chain manually
            for fallback_tier in (ScrapingTier.LIGHTPANDA, ScrapingTier.PLAYWRIGHT, ScrapingTier.PLAYWRIGHT_PROXY):
                await self._rate_limiter.wait(domain)
                fetcher = create_fetcher(fallback_tier)
                result = await retry_async(
                    fetcher.fetch,
                    url,
                    options,
                    max_retries=2,
                    base_delay=2.0,
                    retry_on=(ConnectionError, TimeoutError, OSError),
                )
                self._rate_limiter.report_result(domain, result.status_code)
                if not result.blocked:
                    return result
                self.log.info(
                    "fetch_no_pool_escalating",
                    url=url,
                    from_tier=fallback_tier.value,
                )
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

            if not self._escalation.should_escalate(result):
                await self._escalation.record_result(self.domain, result, success=True)
                return result

            next_tier = self._escalation.get_next_tier(current_tier)
            if next_tier is None:
                await self._escalation.record_result(self.domain, result, success=False)
                return result

            reason = self._escalation.get_escalation_reason(result)
            self.log.info(
                "fetch_escalating",
                url=url,
                from_tier=current_tier.value,
                to_tier=next_tier.value,
                reason=reason,
                status=result.status_code,
                html_size=len(result.html),
            )
            current_tier = next_tier

    async def export_results(self, data: list[dict]) -> int:
        from uuid import UUID

        from src.db.pool import get_pool
        from src.db.queries.scraped_data import batch_insert_scraped_data

        # Inject org_id and user_id into each record if the worker has them
        if self.org_id:
            org_uuid = UUID(self.org_id) if isinstance(self.org_id, str) else self.org_id
            for rec in data:
                rec.setdefault("org_id", org_uuid)
        if self.user_id:
            user_uuid = UUID(self.user_id) if isinstance(self.user_id, str) else self.user_id
            for rec in data:
                rec.setdefault("user_id", user_uuid)

        pool = self._pool or await get_pool()
        return await batch_insert_scraped_data(pool, data)  # type: ignore[arg-type]
