from datetime import UTC
from uuid import UUID

from src.models.scraped_data import BlogUrlMetadata, DataType, ScrapedData
from src.workers.base import BaseWorker


class BlogExtractorWorker(BaseWorker):
    """Extracts blog URLs and article links from blog landing pages."""

    def __init__(self, domain: str, job_id: str):
        super().__init__(domain=domain, job_id=job_id)

    async def execute(self, urls: list[str]) -> list[ScrapedData]:
        if not urls:
            self.log.info("no_blog_urls_to_process")
            return []

        self.log.info("extracting_blogs", url_count=len(urls))
        results: list[ScrapedData] = []

        for url in urls:
            try:
                fetch_result = await self.fetch_page(url)
                if fetch_result.blocked:
                    self.log.warning("blocked", url=url)
                    continue

                # Parse blog page for article links
                from src.scraping.parser.html_parser import HtmlParser

                parser = HtmlParser(fetch_result.html, url)
                article_links = parser.extract_links(
                    selectors=[
                        "article a",
                        "h2 a",
                        ".post-title a",
                        ".entry-title a",
                        "a[rel='bookmark']",
                    ],
                    base_url=url,
                )

                metadata = BlogUrlMetadata(
                    blog_landing_url=url,
                    article_urls=article_links,
                    total_articles=len(article_links),
                )

                # Store result
                from datetime import datetime

                record = {
                    "job_id": UUID(self.job_id),
                    "domain": self.domain,
                    "data_type": DataType.BLOG_URL,
                    "url": url,
                    "title": parser.extract_title(),
                    "metadata": metadata.model_dump(),
                }
                await self.export_results([record])

                results.append(
                    ScrapedData(
                        id=UUID(int=0),  # placeholder
                        job_id=UUID(self.job_id),
                        domain=self.domain,
                        data_type=DataType.BLOG_URL,
                        url=url,
                        title=parser.extract_title(),
                        metadata=metadata.model_dump(),
                        scraped_at=datetime.now(UTC),
                    )
                )

            except Exception as e:
                self.log.error("blog_extract_error", url=url, error=str(e))

        self.log.info("blogs_extracted", count=len(results))
        return results
