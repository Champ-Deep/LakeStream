"""Worker for extracting pricing information from B2B pricing pages."""

from datetime import UTC, datetime
from uuid import UUID

from src.models.scraped_data import DataType, PricingMetadata, ScrapedData
from src.workers.base import BaseWorker


class PricingFinderWorker(BaseWorker):
    """Discovers and extracts pricing plans from pricing pages."""

    def __init__(self, domain: str, job_id: str):
        super().__init__(domain=domain, job_id=job_id)

    async def execute(self, urls: list[str]) -> list[ScrapedData]:
        if not urls:
            self.log.info("no_pricing_urls")
            return []

        self.log.info("finding_pricing", url_count=len(urls))
        results: list[ScrapedData] = []

        for url in urls:
            try:
                fetch_result = await self.fetch_page(url)
                if fetch_result.blocked:
                    continue

                from src.scraping.parser.pricing_parser import PricingParser

                parser = PricingParser(fetch_result.html, url)
                plans = parser.extract_pricing_plans()

                for plan in plans:
                    metadata = PricingMetadata(**plan)
                    record = {
                        "job_id": UUID(self.job_id),
                        "domain": self.domain,
                        "data_type": DataType.PRICING,
                        "url": url,
                        "title": plan.get("plan_name"),
                        "metadata": metadata.model_dump(),
                    }
                    await self.export_results([record])

                    results.append(
                        ScrapedData(
                            id=UUID(int=0),
                            job_id=UUID(self.job_id),
                            domain=self.domain,
                            data_type=DataType.PRICING,
                            url=url,
                            title=plan.get("plan_name"),
                            metadata=metadata.model_dump(),
                            scraped_at=datetime.now(UTC),
                        )
                    )

            except Exception as e:
                self.log.error("pricing_find_error", url=url, error=str(e))

        self.log.info("pricing_found", count=len(results))
        return results
