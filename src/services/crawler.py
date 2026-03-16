import asyncio
from collections import deque
from urllib.parse import urljoin, urlparse

import httpx
import structlog
from selectolax.parser import HTMLParser

from src.models.scraping import FetchOptions, FetchResult, ScrapingTier
from src.scraping.fetcher.factory import create_fetcher
from src.utils.url import ensure_scheme, is_valid_scrape_url, normalize_url

log = structlog.get_logger()

SITEMAP_PATHS = ["/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml"]


class CrawlerService:
    """Native domain crawler and URL discovery engine."""

    def __init__(self, max_concurrent: int = 15, max_per_domain: int = 6, timeout: int = 30000):
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

    async def map_domain(self, domain: str, limit: int = 100) -> list[str]:
        """Discover all valid URLs on a domain."""
        base_url = ensure_scheme(domain)
        self.log.info("mapping_domain", domain=domain, limit=limit)

        # 1. Try sitemaps first (most efficient)
        sitemap_urls = await self._try_sitemap(base_url)
        if sitemap_urls:
            self.log.info("sitemap_found", urls=len(sitemap_urls))
            if len(sitemap_urls) >= limit:
                return list(sitemap_urls)[:limit]
            # Supplement with crawl if sitemap didn't fill the quota
            remaining = limit - len(sitemap_urls)
            self.log.info(
                "supplementing_with_crawl",
                sitemap_count=len(sitemap_urls),
                remaining=remaining,
            )
            crawled = await self._crawl_recursive(base_url, remaining, exclude=sitemap_urls)
            combined = list(sitemap_urls) + crawled
            return combined[:limit]

        # 2. Fallback to native recursive crawl
        return await self._crawl_recursive(base_url, limit)

    async def _try_sitemap(self, base_url: str) -> set[str]:
        """Attempt to find and parse sitemaps, including sitemap indexes."""
        import re

        all_urls: set[str] = set()

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            for path in SITEMAP_PATHS:
                sitemap_url = urljoin(base_url, path)
                try:
                    r = await client.get(sitemap_url)
                    if r.status_code != 200:
                        continue

                    # Check if this is a sitemap index (contains <sitemap><loc> entries)
                    child_sitemaps = re.findall(r"<sitemap>\s*<loc>(.*?)</loc>", r.text)
                    if child_sitemaps:
                        self.log.info(
                            "sitemap_index_found", path=path, children=len(child_sitemaps)
                        )
                        for child_url in child_sitemaps:
                            try:
                                cr = await client.get(child_url)
                                if cr.status_code == 200:
                                    urls = re.findall(r"<loc>(.*?)</loc>", cr.text)
                                    all_urls.update(u for u in urls if is_valid_scrape_url(u))
                            except Exception:
                                continue
                    else:
                        # Regular sitemap — extract page URLs directly
                        urls = re.findall(r"<loc>(.*?)</loc>", r.text)
                        all_urls.update(u for u in urls if is_valid_scrape_url(u))

                    if all_urls:
                        self.log.info("sitemap_parsed", path=path, total_urls=len(all_urls))
                        break  # Found a working sitemap, no need to try others

                except Exception as e:
                    self.log.debug("sitemap_not_found", url=sitemap_url, error=str(e))

        return all_urls

    async def _crawl_recursive(
        self, base_url: str, limit: int, exclude: set[str] | None = None
    ) -> list[str]:
        """Recursively crawl the domain up to the limit.

        Improvements over naive BFS:
        - Logs blocked pages instead of silently skipping
        - Stall detection: exits early if 3 consecutive batches find 0 new URLs
        - Reduced timeout (15s) for faster discovery
        """
        parsed_base = urlparse(base_url)
        base_domain = parsed_base.netloc.lower().replace("www.", "")

        discovered: set[str] = set(exclude) if exclude else set()
        discovered.add(base_url)
        to_crawl: deque[str] = deque([base_url])
        crawled: set[str] = set()
        new_urls: list[str] = []  # Only URLs not in exclude
        blocked_count = 0
        stall_batches = 0  # Consecutive batches with zero new URLs

        fetcher = create_fetcher(ScrapingTier.PLAYWRIGHT)
        options = FetchOptions(timeout=min(self.timeout, 15000))  # 15s max for discovery
        sem = self._get_semaphore(base_domain)

        async def _fetch_with_limit(url: str):
            async with sem:
                return await fetcher.fetch(url, options)

        while to_crawl and len(new_urls) < limit:
            batch_size = min(self.max_concurrent, len(to_crawl))
            batch = [to_crawl.popleft() for _ in range(batch_size)]

            tasks = [_fetch_with_limit(u) for u in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            batch_new = 0
            for result in results:
                if isinstance(result, Exception):
                    continue
                if result.blocked or not result.html:
                    blocked_count += 1
                    self.log.debug(
                        "crawl_blocked",
                        url=result.url if hasattr(result, "url") else "unknown",
                        status=result.status_code if hasattr(result, "status_code") else 0,
                    )
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
                            if not exclude or full_url not in exclude:
                                new_urls.append(full_url)
                                batch_new += 1
                            if full_url not in crawled:
                                to_crawl.append(full_url)

                    if len(new_urls) >= limit:
                        break

            # Stall detection: only count stalls when pages were fetched successfully
            # but yielded no new URLs (graph exhausted). Blocked batches don't count —
            # the queue may still have good URLs behind the blocked ones.
            batch_fetched = sum(
                1 for r in results
                if not isinstance(r, Exception) and not r.blocked and r.html
            )
            if batch_new == 0 and batch_fetched > 0:
                stall_batches += 1
                if stall_batches >= 3:
                    self.log.info(
                        "crawl_stalled",
                        new_urls=len(new_urls),
                        blocked=blocked_count,
                        remaining_queue=len(to_crawl),
                    )
                    break
            elif batch_new > 0:
                stall_batches = 0

            await asyncio.sleep(0)  # yield control without delay

        if blocked_count > 0:
            self.log.info(
                "crawl_complete",
                new_urls=len(new_urls),
                blocked=blocked_count,
                crawled=len(crawled),
            )

        return new_urls[:limit]
