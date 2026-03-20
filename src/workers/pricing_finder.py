"""Worker for extracting pricing information from B2B pricing pages."""

from datetime import UTC, datetime
from uuid import UUID

from src.models.scraped_data import DataType, PricingMetadata, ScrapedData
from src.workers.base import BaseWorker


class PricingFinderWorker(BaseWorker):
    """Discovers and extracts pricing plans from pricing pages."""

    def __init__(
        self,
        domain: str,
        job_id: str,
        pool: object | None = None,
        org_id: str | None = None,
        user_id: str | None = None,
        tier_override: str | None = None,
        proxy_url: str | None = None,
    ):
        super().__init__(
            domain=domain,
            job_id=job_id,
            pool=pool,
            org_id=org_id,
            user_id=user_id,
            tier_override=tier_override,
            proxy_url=proxy_url,
        )

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

                # Extract rich metadata (og:, twitter:, meta: tags) - once per page
                from src.scraping.parser.html_parser import extract_rich_metadata
                rich_meta = extract_rich_metadata(fetch_result.html, url)

                from src.scraping.parser.pricing_parser import PricingParser

                parser = PricingParser(fetch_result.html, url)
                plans = parser.extract_pricing_plans()

                for plan in plans:
                    metadata = PricingMetadata(**plan)
                    # Merge rich metadata with pricing-specific metadata
                    combined_metadata = {
                        **rich_meta,  # Rich metadata (og:, twitter:, etc.)
                        **metadata.model_dump(),  # Pricing metadata
                    }
                    record = {
                        "job_id": UUID(self.job_id),
                        "domain": self.domain,
                        "data_type": DataType.PRICING,
                        "url": url,
                        "title": plan.get("plan_name"),
                        "metadata": combined_metadata,
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
                            metadata=combined_metadata,
                            scraped_at=datetime.now(UTC),
                        )
                    )

            except Exception as e:
                self.log.error("pricing_find_error", url=url, error=str(e))

        self.log.info("pricing_found", count=len(results))
        return results
