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

    def __init__(self, max_concurrent: int = 10, max_per_domain: int = 2, timeout: int = 30000):
        self.max_concurrent = max_concurrent
        self.max_per_domain = max_per_domain
        self.timeout = timeout
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

    async def map_domain(self, domain: str, limit: int = 10000) -> list[str]:
        """Discover all valid URLs on a domain."""
        base_url = ensure_scheme(domain)
        self.log.info("mapping_domain", domain=domain, limit=limit)

        # 1. Try sitemap.xml first (most efficient)
        sitemap_urls = await self._try_sitemap(base_url)
        if sitemap_urls:
            self.log.info("sitemap_found", urls=len(sitemap_urls))
            return list(sitemap_urls)[:limit]

        # 2. Fallback to native recursive crawl
        return await self._crawl_recursive(base_url, limit)

    async def _try_sitemap(self, base_url: str) -> set[str]:
        """Attempt to find and parse sitemap.xml, including sitemap index files."""
        import re

        sitemap_url = urljoin(base_url, "/sitemap.xml")
        all_urls: set[str] = set()

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                r = await client.get(sitemap_url)
                if r.status_code != 200:
                    return set()

                content = r.text

                # Check if this is a sitemap index (contains <sitemapindex> or links to other sitemaps)
                if "<sitemapindex" in content or "sitemap.xml" in content.lower():
                    # This is a sitemap index - extract all child sitemap URLs
                    child_sitemaps = re.findall(r"<loc>(.*?)</loc>", content)
                    child_sitemaps = [s for s in child_sitemaps if "sitemap" in s.lower() and s.endswith(".xml")]

                    self.log.info("sitemap_index_found", child_count=len(child_sitemaps))

                    # Fetch all child sitemaps concurrently
                    for child_url in child_sitemaps:
                        try:
                            child_r = await client.get(child_url)
                            if child_r.status_code == 200:
                                child_urls = re.findall(r"<loc>(.*?)</loc>", child_r.text)
                                valid_urls = {u for u in child_urls if is_valid_scrape_url(u) and not u.endswith(".xml")}
                                all_urls.update(valid_urls)
                                self.log.debug("child_sitemap_parsed", url=child_url, urls_found=len(valid_urls))
                        except Exception as e:
                            self.log.debug("child_sitemap_error", url=child_url, error=str(e))
                            continue
                else:
                    # Regular sitemap - extract URLs directly
                    urls = re.findall(r"<loc>(.*?)</loc>", content)
                    all_urls = {u for u in urls if is_valid_scrape_url(u)}

        except Exception as e:
            self.log.debug("sitemap_not_found", url=sitemap_url, error=str(e))

        self.log.info("sitemap_total_urls", count=len(all_urls))
        return all_urls

    async def _crawl_recursive(self, base_url: str, limit: int) -> list[str]:
        """Recursively crawl the domain up to the limit."""
        parsed_base = urlparse(base_url)
        base_domain = parsed_base.netloc.lower().replace("www.", "")

        discovered: set[str] = {base_url}
        to_crawl: list[str] = [base_url]
        crawled: set[str] = set()

        fetcher = create_fetcher(ScrapingTier.PLAYWRIGHT_PROXY)
        options = FetchOptions(timeout=self.timeout)
        sem = self._get_semaphore(base_domain)

        async def _fetch_with_limit(url: str):
            async with sem:
                return await fetcher.fetch(url, options)

        while to_crawl and len(discovered) < limit:
            batch_size = min(self.max_concurrent, len(to_crawl))
            batch = [to_crawl.pop(0) for _ in range(batch_size)]

            tasks = [_fetch_with_limit(u) for u in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    continue
                if result.blocked or not result.html:
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
                        if full_url not in discovered:
                            discovered.add(full_url)
                            if full_url not in crawled and full_url not in to_crawl:
                                to_crawl.append(full_url)

                    if len(discovered) >= limit:
                        break

            await asyncio.sleep(0.1)

        return list(discovered)[:limit]
