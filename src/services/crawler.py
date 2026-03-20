import asyncio
from urllib.parse import urljoin, urlparse

import httpx
import structlog
from selectolax.parser import HTMLParser

from src.models.scraping import FetchOptions, FetchResult, ScrapingTier
from src.scraping.fetcher.factory import create_fetcher
from src.utils.url import ensure_scheme, is_valid_scrape_url, normalize_url

log = structlog.get_logger()


class CrawlerService:
    """Native domain crawler and URL discovery engine."""

    def __init__(self, max_concurrent: int = 10, max_per_domain: int = 2, timeout: int = 30000, pool=None, job_id: str | None = None):
        self.max_concurrent = max_concurrent
        self.max_per_domain = max_per_domain
        self.timeout = timeout
        self.pool = pool
        self.job_id = job_id
        self._domain_semaphores: dict[str, asyncio.Semaphore] = {}
        self.log = log.bind(service="CrawlerService")

    def _get_semaphore(self, domain: str) -> asyncio.Semaphore:
        """Get or create a semaphore for the given domain."""
        if domain not in self._domain_semaphores:
            self._domain_semaphores[domain] = asyncio.Semaphore(self.max_per_domain)
        return self._domain_semaphores[domain]

    async def fetch_with_limit(self, url: str, fetcher, options: FetchOptions) -> FetchResult:
        """Fetch a URL with per-domain concurrency limiting."""
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        sem = self._get_semaphore(domain)
        async with sem:
            return await fetcher.fetch(url, options)

    async def map_domain(self, domain: str, limit: int | None = None) -> list[str]:
        """Discover all valid URLs on a domain.

        Args:
            domain: Domain to crawl
            limit: Optional limit (None for unlimited scraping)

        Returns:
            List of all discovered URLs (including duplicates in traversal paths)
        """
        base_url = ensure_scheme(domain)
        self.log.info("mapping_domain", domain=domain, limit=limit or "unlimited")

        # 1. Try sitemap.xml first (most efficient)
        sitemap_urls = await self._try_sitemap(base_url)
        if sitemap_urls:
            self.log.info("sitemap_found", urls=len(sitemap_urls))
            # Return ALL sitemap URLs when unlimited
            if limit is None:
                return list(sitemap_urls)
            return list(sitemap_urls)[:limit]

        # 2. Fallback to native recursive crawl
        return await self._crawl_recursive(base_url, limit)

    async def _try_sitemap(self, base_url: str) -> set[str]:
        """Attempt to find and parse sitemap.xml."""
        sitemap_url = urljoin(base_url, "/sitemap.xml")
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                r = await client.get(sitemap_url)
                if r.status_code == 200:
                    import re

                    urls = re.findall(r"<loc>(.*?)</loc>", r.text)
                    return {u for u in urls if is_valid_scrape_url(u)}
        except Exception as e:
            self.log.debug("sitemap_not_found", url=sitemap_url, error=str(e))
        return set()

    async def _crawl_recursive(self, base_url: str, limit: int | None) -> list[str]:
        """Recursively crawl the domain.

        Args:
            base_url: Starting URL
            limit: Optional limit (None for unlimited crawling)

        Returns:
            List of all discovered URLs with full traversal path tracking
        """
        parsed_base = urlparse(base_url)
        base_domain = parsed_base.netloc.lower().replace("www.", "")

        # Track all discovered URLs (for deduplication)
        discovered: set[str] = {base_url}
        # Track all URLs in traversal order (including duplicates from different paths)
        traversal_paths: list[str] = [base_url]
        to_crawl: list[str] = [base_url]
        crawled: set[str] = set()

        # Use PLAYWRIGHT_PROXY for crawling (best compatibility)
        fetcher = create_fetcher(ScrapingTier.PLAYWRIGHT_PROXY)
        options = FetchOptions(timeout=self.timeout)
        sem = self._get_semaphore(base_domain)

        async def _fetch_with_limit(url: str):
            async with sem:
                return await fetcher.fetch(url, options)

        # Continue until no more URLs to crawl (unlimited) or hit limit
        while to_crawl:
            # Check limit only if specified
            if limit is not None and len(discovered) >= limit:
                break

            batch_size = min(self.max_concurrent, len(to_crawl))
            batch = [to_crawl.pop(0) for _ in range(batch_size)]

            tasks = [_fetch_with_limit(u) for u in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    self.log.warning("crawl_error", error=str(result))
                    continue
                if result.blocked or not result.html:
                    self.log.debug("crawl_blocked_or_empty", url=result.url, blocked=result.blocked)
                    continue

                crawled.add(result.url)
                parser = HTMLParser(result.html)

                for a in parser.css("a[href]"):
                    href = a.attributes.get("href")
                    if not href:
                        continue

                    full_url = normalize_url(href, result.url)
                    parsed_link = urlparse(full_url)
                    link_domain = parsed_link.netloc.lower().replace("www.", "")

                    if link_domain == base_domain and is_valid_scrape_url(full_url):
                        # Track in traversal path even if it's a duplicate
                        traversal_paths.append(full_url)

                        # Add to crawl queue if not yet discovered
                        if full_url not in discovered:
                            discovered.add(full_url)
                            if full_url not in crawled and full_url not in to_crawl:
                                to_crawl.append(full_url)

                    # Break if we hit limit
                    if limit is not None and len(discovered) >= limit:
                        break

                if limit is not None and len(discovered) >= limit:
                    break

            # No sleep - unlimited speed scraping
            self.log.info(
                "crawl_progress",
                discovered=len(discovered),
                to_crawl=len(to_crawl),
                crawled=len(crawled),
                limit=limit or "unlimited"
            )

            # Update database with real-time progress
            if self.pool and self.job_id:
                try:
                    from uuid import UUID
                    await self.pool.execute(
                        "UPDATE scrape_jobs SET pages_scraped = $1 WHERE id = $2",
                        len(crawled),
                        UUID(self.job_id)
                    )
                except Exception as e:
                    self.log.warning("failed_to_update_progress", error=str(e))

        # Return all traversal paths (includes duplicates from different paths)
        self.log.info("crawl_complete", total_urls=len(discovered), traversal_paths=len(traversal_paths))
        if limit is None:
            return traversal_paths
        return list(discovered)[:limit]
