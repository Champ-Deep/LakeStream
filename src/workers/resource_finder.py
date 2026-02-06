from datetime import UTC, datetime
from uuid import UUID

from src.models.scraped_data import DataType, ResourceMetadata, ScrapedData
from src.workers.base import BaseWorker


class ResourceFinderWorker(BaseWorker):
    """Discovers whitepapers, case studies, webinars, and other resources."""

    def __init__(self, domain: str, job_id: str):
        super().__init__(domain=domain, job_id=job_id)

    async def execute(self, urls: list[str]) -> list[ScrapedData]:
        if not urls:
            self.log.info("no_resource_urls")
            return []

        self.log.info("finding_resources", url_count=len(urls))
        results: list[ScrapedData] = []

        for url in urls:
            try:
                fetch_result = await self.fetch_page(url)
                if fetch_result.blocked:
                    continue

                from src.scraping.parser.resource_parser import ResourceParser

                parser = ResourceParser(fetch_result.html, url)
                resources = parser.extract_resources()

                for resource in resources:
                    metadata = ResourceMetadata(**resource)
                    record = {
                        "job_id": UUID(self.job_id),
                        "domain": self.domain,
                        "data_type": DataType.RESOURCE,
                        "url": resource.get("url", url),
                        "title": resource.get("title"),
                        "metadata": metadata.model_dump(),
                    }
                    await self.export_results([record])

                    results.append(
                        ScrapedData(
                            id=UUID(int=0),
                            job_id=UUID(self.job_id),
                            domain=self.domain,
                            data_type=DataType.RESOURCE,
                            url=resource.get("url", url),
                            title=resource.get("title"),
                            metadata=metadata.model_dump(),
                            scraped_at=datetime.now(UTC),
                        )
                    )

            except Exception as e:
                self.log.error("resource_find_error", url=url, error=str(e))

        self.log.info("resources_found", count=len(results))
        return results
