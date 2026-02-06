from datetime import UTC, datetime
from uuid import UUID

from src.models.scraped_data import DataType, ScrapedData, TechStackMetadata
from src.workers.base import BaseWorker


class TechDetectorWorker(BaseWorker):
    """Detects technology stack from page source code."""

    def __init__(self, domain: str, job_id: str):
        super().__init__(domain=domain, job_id=job_id)

    async def execute(self, urls: list[str]) -> list[ScrapedData]:
        if not urls:
            return []

        self.log.info("detecting_tech_stack", url_count=len(urls))

        # Analyze first URL (usually homepage)
        url = urls[0]
        try:
            fetch_result = await self.fetch_page(url)
            if fetch_result.blocked:
                self.log.warning("blocked", url=url)
                return []

            from src.scraping.parser.tech_parser import TechParser

            parser = TechParser(fetch_result.html, fetch_result.headers)
            detected = parser.detect()

            metadata = TechStackMetadata(
                platform=detected.get("platform"),
                js_libraries=detected.get("js_libraries", []),
                analytics=detected.get("analytics", []),
                marketing_tools=detected.get("marketing_tools", []),
                frameworks=detected.get("frameworks", []),
            )

            record = {
                "job_id": UUID(self.job_id),
                "domain": self.domain,
                "data_type": DataType.TECH_STACK,
                "url": url,
                "title": f"Tech Stack: {self.domain}",
                "metadata": metadata.model_dump(),
            }
            await self.export_results([record])

            return [
                ScrapedData(
                    id=UUID(int=0),
                    job_id=UUID(self.job_id),
                    domain=self.domain,
                    data_type=DataType.TECH_STACK,
                    url=url,
                    title=record["title"],
                    metadata=metadata.model_dump(),
                    scraped_at=datetime.now(UTC),
                )
            ]

        except Exception as e:
            self.log.error("tech_detect_error", url=url, error=str(e))
            return []
