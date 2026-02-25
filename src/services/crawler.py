import asyncio
from typing import Set, List
from urllib.parse import urljoin, urlparse

import httpx
import structlog
from selectolax.parser import HTMLParser

from src.models.scraping import FetchOptions, ScrapingTier
from src.scraping.fetcher.factory import create_fetcher
from src.utils.url import ensure_scheme, is_valid_scrape_url, normalize_url

log = structlog.get_logger()

class CrawlerService:
    """Native domain crawler and URL discovery engine."""

    def __init__(self, max_concurrent: int = 10, timeout: int = 30000):
        self.max_concurrent = max_concurrent
        self.timeout = timeout
        self.log = log.bind(service="CrawlerService")

    async def map_domain(self, domain: str, limit: int = 100) -> List[str]:
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

    async def _try_sitemap(self, base_url: str) -> Set[str]:
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

    async def _crawl_recursive(self, base_url: str, limit: int) -> List[str]:
        """Recursively crawl the domain up to the limit."""
        parsed_base = urlparse(base_url)
        base_domain = parsed_base.netloc.lower().replace("www.", "")
        
        discovered: Set[str] = {base_url}
        to_crawl: List[str] = [base_url]
        crawled: Set[str] = set()

        fetcher = create_fetcher(ScrapingTier.BASIC_HTTP)
        options = FetchOptions(timeout=self.timeout)

        while to_crawl and len(discovered) < limit:
            batch_size = min(self.max_concurrent, len(to_crawl))
            batch = [to_crawl.pop(0) for _ in range(batch_size)]
            
            tasks = [fetcher.fetch(u, options) for u in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception) or result.blocked or not result.html:
                    continue
                
                crawled.add(result.url)
                parser = HTMLParser(result.html)
                
                for a in parser.css("a[href]"):
                    href = a.attributes.get("href")
                    if not href: continue
                        
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
