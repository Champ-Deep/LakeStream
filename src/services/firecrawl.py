import json
import warnings
from pathlib import Path
from typing import Any

import structlog

from src.config.constants import FIRECRAWL_OUTPUT_DIR
from src.config.settings import get_settings
from src.services.crawler import CrawlerService
from src.services.lakecurrent import LakeCurrentClient
from src.services.scraper import ScraperService

log = structlog.get_logger()


class FirecrawlService:
    """DEPRECATED: This class is kept for backward compatibility only.

    Use CrawlerService directly for domain mapping and ScraperService for page scraping.
    This wrapper will be removed in a future version.
    """

    def __init__(self, output_dir: str = FIRECRAWL_OUTPUT_DIR):
        warnings.warn(
            "FirecrawlService is deprecated and will be removed in a future version. "
            "Use CrawlerService for domain mapping and ScraperService for scraping.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.output_dir = output_dir
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        self.crawler = CrawlerService()
        self.scraper = ScraperService()
        self._settings = get_settings()

    async def map_domain(self, url: str, limit: int = 100) -> list[str]:
        """Discover all URLs on a domain using native CrawlerService."""
        return await self.crawler.map_domain(url, limit)

    async def scrape_page(
        self,
        url: str,
        output_file: str | None = None,
        *,
        only_main_content: bool = True,
        wait_for: int | None = None,
    ) -> str:
        """Scrape a single page using native ScraperService."""
        result = await self.scraper.scrape(url, only_main_content=only_main_content)

        markdown = result.get("markdown", "")

        if output_file:
            with open(output_file, "w") as f:
                f.write(markdown)

        return markdown

    async def search(
        self,
        query: str,
        output_file: str | None = None,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search the web using LakeCurrent (Firecrawl search alternative)."""
        client = LakeCurrentClient(
            base_url=self._settings.lakecurrent_base_url, timeout=self._settings.lakecurrent_timeout
        )
        try:
            results = await client.search(query, limit=limit)
            raw_results = [r.model_dump() for r in results.results]

            if output_file:
                with open(output_file, "w") as f:
                    json.dump(raw_results, f)

            return raw_results
        finally:
            await client.close()
