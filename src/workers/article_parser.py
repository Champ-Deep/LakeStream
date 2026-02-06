from datetime import UTC, datetime
from uuid import UUID

from src.models.scraped_data import ArticleMetadata, DataType, ScrapedData
from src.workers.base import BaseWorker


class ArticleParserWorker(BaseWorker):
    """Extracts metadata from individual article pages."""

    def __init__(self, domain: str, job_id: str):
        super().__init__(domain=domain, job_id=job_id)

    async def execute(self, urls: list[str]) -> list[ScrapedData]:
        if not urls:
            self.log.info("no_articles_to_parse")
            return []

        self.log.info("parsing_articles", count=len(urls))
        results: list[ScrapedData] = []

        for url in urls:
            try:
                fetch_result = await self.fetch_page(url)
                if fetch_result.blocked:
                    continue

                from src.scraping.parser.html_parser import HtmlParser

                parser = HtmlParser(fetch_result.html, url)

                metadata = ArticleMetadata(
                    author=parser.extract_meta("author"),
                    categories=parser.extract_categories(),
                    word_count=parser.count_words(),
                    excerpt=parser.extract_meta("description"),
                )

                record = {
                    "job_id": UUID(self.job_id),
                    "domain": self.domain,
                    "data_type": DataType.ARTICLE,
                    "url": url,
                    "title": parser.extract_title(),
                    "metadata": metadata.model_dump(),
                }
                await self.export_results([record])

                results.append(
                    ScrapedData(
                        id=UUID(int=0),
                        job_id=UUID(self.job_id),
                        domain=self.domain,
                        data_type=DataType.ARTICLE,
                        url=url,
                        title=parser.extract_title(),
                        metadata=metadata.model_dump(),
                        scraped_at=datetime.now(UTC),
                    )
                )

            except Exception as e:
                self.log.error("article_parse_error", url=url, error=str(e))

        self.log.info("articles_parsed", count=len(results))
        return results
