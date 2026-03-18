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

        # 1. Discover sitemap URLs from robots.txt first, then fall back to /sitemap.xml
        sitemap_urls = await self._try_sitemaps(base_url)
        if sitemap_urls:
            self.log.info("sitemap_found", urls=len(sitemap_urls))
            return list(sitemap_urls)[:limit]

        # 2. Fallback to native recursive crawl
        return await self._crawl_recursive(base_url, limit)

    async def _discover_sitemap_urls_from_robots(self, base_url: str, client: httpx.AsyncClient) -> list[str]:
        """Parse robots.txt and extract all Sitemap: directive URLs.

        Returns a list of sitemap URLs declared in robots.txt, or empty list if
        robots.txt is missing / has no Sitemap directives.
        """
        import re
        robots_url = urljoin(base_url, "/robots.txt")
        sitemap_urls: list[str] = []
        try:
            r = await client.get(robots_url)
            if r.status_code == 200:
                for line in r.text.splitlines():
                    line = line.strip()
                    if line.lower().startswith("sitemap:"):
                        url = line.split(":", 1)[1].strip()
                        if url:
                            sitemap_urls.append(url)
                self.log.info(
                    "robots_txt_sitemaps",
                    base_url=base_url,
                    found=len(sitemap_urls),
                )
        except Exception as e:
            self.log.debug("robots_txt_error", base_url=base_url, error=str(e))
        return sitemap_urls

    async def _fetch_and_parse_sitemap(self, sitemap_url: str, client: httpx.AsyncClient) -> set[str]:
        """Fetch one sitemap (regular or index) and return all content URLs inside it.

        Handles:
        - Regular <urlset> sitemaps
        - <sitemapindex> files pointing to child sitemaps (fetched concurrently)
        """
        import re
        all_urls: set[str] = set()
        try:
            r = await client.get(sitemap_url)
            if r.status_code != 200:
                return set()
            content = r.text

            if "<sitemapindex" in content:
                # Index file — extract child sitemap URLs and fetch them concurrently
                child_sitemaps = re.findall(r"<loc>\s*(.*?)\s*</loc>", content)
                child_sitemaps = [
                    s for s in child_sitemaps
                    if "sitemap" in s.lower() and s.lower().endswith(".xml")
                ]
                self.log.info("sitemap_index_found", url=sitemap_url, child_count=len(child_sitemaps))

                async def _fetch_child(child_url: str) -> set[str]:
                    try:
                        child_r = await client.get(child_url)
                        if child_r.status_code == 200:
                            child_locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", child_r.text)
                            valid = {u for u in child_locs if is_valid_scrape_url(u) and not u.lower().endswith(".xml")}
                            self.log.debug("child_sitemap_parsed", url=child_url, urls_found=len(valid))
                            return valid
                    except Exception as e:
                        self.log.debug("child_sitemap_error", url=child_url, error=str(e))
                    return set()

                # Fetch all child sitemaps concurrently
                child_results = await asyncio.gather(*[_fetch_child(u) for u in child_sitemaps])
                for result in child_results:
                    all_urls.update(result)
            else:
                # Regular urlset sitemap
                locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", content)
                all_urls = {u for u in locs if is_valid_scrape_url(u)}

        except Exception as e:
            self.log.debug("sitemap_fetch_error", url=sitemap_url, error=str(e))

        return all_urls

    async def _try_sitemaps(self, base_url: str) -> set[str]:
        """Discover sitemaps via robots.txt first, then fall back to /sitemap.xml.

        Strategy:
        1. Fetch robots.txt → extract all Sitemap: directive URLs
        2. If none found, try /sitemap.xml, /sitemap_index.xml
        3. Parse each sitemap (handles sitemap index → child sitemaps concurrently)
        """
        all_urls: set[str] = set()

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            # Step 1: get sitemap URLs from robots.txt
            sitemap_locations = await self._discover_sitemap_urls_from_robots(base_url, client)

            # Step 2: if robots.txt had none, try standard locations
            if not sitemap_locations:
                sitemap_locations = [
                    urljoin(base_url, "/sitemap.xml"),
                    urljoin(base_url, "/sitemap_index.xml"),
                ]

            # Step 3: fetch and parse all discovered sitemaps
            for sitemap_url in sitemap_locations:
                urls = await self._fetch_and_parse_sitemap(sitemap_url, client)
                all_urls.update(urls)

        self.log.info("sitemap_total_urls", count=len(all_urls))
        return all_urls

    async def _crawl_recursive(self, base_url: str, limit: int) -> list[str]:
        """Recursively crawl the domain up to the limit."""
        parsed_base = urlparse(base_url)
        base_domain = parsed_base.netloc.lower().replace("www.", "")

        discovered: set[str] = {base_url}
        to_crawl: list[str] = [base_url]
        crawled: set[str] = set()

        fetcher = create_fetcher(ScrapingTier.LIGHTPANDA)
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
