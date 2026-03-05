from datetime import UTC
from urllib.parse import urlparse
from uuid import UUID

from src.models.scraped_data import BlogUrlMetadata, DataType, ScrapedData
from src.utils.url import extract_domain
from src.workers.base import BaseWorker


class BlogExtractorWorker(BaseWorker):
    """Extracts blog URLs and article links from blog landing pages."""

    _SKIP_EXTENSIONS = frozenset({
        ".pdf", ".doc", ".docx", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    })

    def __init__(self, domain: str, job_id: str, pool: object | None = None, org_id: str | None = None):
        super().__init__(domain=domain, job_id=job_id, pool=pool, org_id=org_id)

    def _filter_article_links(self, links: list[str], source_url: str) -> list[str]:
        """Remove homepage, non-HTML, and off-domain links from article candidates."""
        source_domain = extract_domain(source_url)
        filtered = []
        for link in links:
            parsed = urlparse(link)
            path = parsed.path.rstrip("/")
            if not path:
                continue
            if any(path.lower().endswith(ext) for ext in self._SKIP_EXTENSIONS):
                continue
            if extract_domain(link) != source_domain:
                continue
            filtered.append(link)
        return filtered

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
                article_links = self._filter_article_links(article_links, url)

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
