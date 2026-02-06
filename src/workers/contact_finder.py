from datetime import UTC, datetime
from uuid import UUID

from src.models.scraped_data import ContactMetadata, DataType, ScrapedData
from src.workers.base import BaseWorker


class ContactFinderWorker(BaseWorker):
    """Extracts contact and people information from team/about pages."""

    def __init__(self, domain: str, job_id: str):
        super().__init__(domain=domain, job_id=job_id)

    async def execute(self, urls: list[str]) -> list[ScrapedData]:
        if not urls:
            self.log.info("no_contact_pages")
            return []

        self.log.info("finding_contacts", url_count=len(urls))
        results: list[ScrapedData] = []

        for url in urls:
            try:
                fetch_result = await self.fetch_page(url)
                if fetch_result.blocked:
                    continue

                from src.scraping.parser.contact_parser import ContactParser

                parser = ContactParser(fetch_result.html, url)
                people = parser.extract_people()

                for person in people:
                    metadata = ContactMetadata(**person)
                    record = {
                        "job_id": UUID(self.job_id),
                        "domain": self.domain,
                        "data_type": DataType.CONTACT,
                        "url": url,
                        "title": f"{metadata.first_name or ''} {metadata.last_name or ''}".strip()
                        or None,
                        "metadata": metadata.model_dump(),
                    }
                    await self.export_results([record])

                    results.append(
                        ScrapedData(
                            id=UUID(int=0),
                            job_id=UUID(self.job_id),
                            domain=self.domain,
                            data_type=DataType.CONTACT,
                            url=url,
                            title=record["title"],
                            metadata=metadata.model_dump(),
                            scraped_at=datetime.now(UTC),
                        )
                    )

            except Exception as e:
                self.log.error("contact_find_error", url=url, error=str(e))

        self.log.info("contacts_found", count=len(results))
        return results
