import asyncio
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import structlog
from selectolax.parser import HTMLParser

from src.config.constants import FIRECRAWL_OUTPUT_DIR
from src.utils.shell import run_command

log = structlog.get_logger()


def _has_firecrawl_cli() -> bool:
    """Check if firecrawl CLI is available."""
    return shutil.which("firecrawl") is not None


class FirecrawlService:
    """Wraps the Firecrawl CLI for domain mapping and page scraping.

    Falls back to HTTP-based crawling when firecrawl CLI isn't installed.
    """

    def __init__(self, output_dir: str = FIRECRAWL_OUTPUT_DIR):
        self.output_dir = output_dir
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        self._has_cli = _has_firecrawl_cli()
        if not self._has_cli:
            log.warning("firecrawl_cli_not_found", fallback="http_crawler")

    async def map_domain(self, url: str, limit: int = 100) -> list[str]:
        """Discover all URLs on a domain using firecrawl map or HTTP fallback."""
        if self._has_cli:
            return await self._map_domain_cli(url, limit)
        return await self._map_domain_http(url, limit)

    async def _map_domain_cli(self, url: str, limit: int) -> list[str]:
        """Use firecrawl CLI to map domain."""
        safe_name = url.replace("https://", "").replace("http://", "").replace("/", "_")
        output_file = f"{self.output_dir}/{safe_name}-map.json"

        stdout, stderr, returncode = await run_command(
            "firecrawl",
            "map",
            url,
            "--json",
            "--limit",
            str(limit),
            "-o",
            output_file,
            timeout=120,
        )

        if returncode != 0:
            log.error("firecrawl_map_failed", url=url, stderr=stderr[:500])
            try:
                data = json.loads(stdout)
                return data.get("urls", data.get("links", []))
            except json.JSONDecodeError:
                return []

        try:
            with open(output_file) as f:
                data = json.load(f)
            urls = data if isinstance(data, list) else data.get("urls", data.get("links", []))
            log.info("firecrawl_map_success", url=url, urls_found=len(urls))
            return urls
        except (json.JSONDecodeError, FileNotFoundError) as e:
            log.error("firecrawl_map_parse_error", url=url, error=str(e))
            return []

    async def _map_domain_http(self, url: str, limit: int) -> list[str]:
        """HTTP fallback for domain mapping - simple link extraction."""
        parsed = urlparse(url)
        base_domain = parsed.netloc.lower().replace("www.", "")
        discovered: set[str] = set()
        to_crawl: list[str] = [url]
        crawled: set[str] = set()

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }

        log.info("http_map_starting", url=url, limit=limit)

        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True, headers=headers
        ) as client:
            while to_crawl and len(discovered) < limit:
                # Process up to 5 pages concurrently
                batch = []
                while to_crawl and len(batch) < 5:
                    next_url = to_crawl.pop(0)
                    if next_url not in crawled:
                        batch.append(next_url)
                        crawled.add(next_url)

                if not batch:
                    break

                tasks = [self._fetch_and_extract_links(client, u, base_domain) for u in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for result in results:
                    if isinstance(result, Exception):
                        continue
                    for link in result:
                        if link not in discovered:
                            discovered.add(link)
                            if link not in crawled and len(to_crawl) < 50:
                                to_crawl.append(link)

                # Rate limiting
                await asyncio.sleep(0.5)

        urls = list(discovered)[:limit]
        log.info("http_map_success", url=url, urls_found=len(urls))
        return urls

    async def _fetch_and_extract_links(
        self, client: httpx.AsyncClient, url: str, base_domain: str
    ) -> list[str]:
        """Fetch page and extract same-domain links."""
        try:
            response = await client.get(url)
            if response.status_code >= 400:
                return []

            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type:
                return []

            parser = HTMLParser(response.text)
            links: list[str] = []

            for a_tag in parser.css("a[href]"):
                href = a_tag.attributes.get("href", "")
                if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    continue

                # Resolve relative URLs
                full_url = urljoin(url, href)
                parsed = urlparse(full_url)

                # Only include same-domain links
                link_domain = parsed.netloc.lower().replace("www.", "")
                if link_domain == base_domain:
                    # Clean URL
                    clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                    if clean_url.endswith("/"):
                        clean_url = clean_url[:-1]
                    links.append(clean_url)

            return list(set(links))
        except Exception as e:
            log.debug("link_extraction_error", url=url, error=str(e))
            return []

    async def scrape_page(
        self,
        url: str,
        output_file: str | None = None,
        *,
        only_main_content: bool = True,
        wait_for: int | None = None,
    ) -> str:
        """Scrape a single page using firecrawl scrape or HTTP fallback."""
        if self._has_cli:
            return await self._scrape_page_cli(url, output_file, only_main_content, wait_for)
        return await self._scrape_page_http(url, only_main_content)

    async def _scrape_page_cli(
        self,
        url: str,
        output_file: str | None,
        only_main_content: bool,
        wait_for: int | None,
    ) -> str:
        """Use firecrawl CLI to scrape page."""
        if output_file is None:
            safe_name = url.replace("https://", "").replace("http://", "").replace("/", "_")
            output_file = f"{self.output_dir}/{safe_name}.md"

        args = ["firecrawl", "scrape", url, "-o", output_file]
        if only_main_content:
            args.append("--only-main-content")
        if wait_for:
            args.extend(["--wait-for", str(wait_for)])

        stdout, stderr, returncode = await run_command(*args, timeout=60)

        if returncode != 0:
            log.error("firecrawl_scrape_failed", url=url, stderr=stderr[:500])
            return ""

        try:
            with open(output_file) as f:
                return f.read()
        except FileNotFoundError:
            return stdout

    async def _scrape_page_http(self, url: str, only_main_content: bool) -> str:
        """HTTP fallback for page scraping - returns HTML as markdown-ish text."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }

        try:
            async with httpx.AsyncClient(
                timeout=30.0, follow_redirects=True, headers=headers
            ) as client:
                response = await client.get(url)
                if response.status_code >= 400:
                    log.error("http_scrape_failed", url=url, status=response.status_code)
                    return ""

                parser = HTMLParser(response.text)

                # Extract main content if requested
                if only_main_content:
                    # Try to find main content areas
                    main = (
                        parser.css_first("main")
                        or parser.css_first("article")
                        or parser.css_first('[role="main"]')
                        or parser.css_first(".content")
                        or parser.css_first("#content")
                    )
                    if main:
                        return self._html_to_text(main)

                # Fall back to body
                body = parser.css_first("body")
                if body:
                    return self._html_to_text(body)

                return response.text

        except Exception as e:
            log.error("http_scrape_error", url=url, error=str(e))
            return ""

    def _html_to_text(self, node) -> str:
        """Convert HTML node to readable text."""
        # Remove script/style tags
        for tag in node.css("script, style, nav, footer, header, aside"):
            tag.decompose()

        text = node.text(separator="\n", strip=True)
        # Clean up multiple newlines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    async def search(
        self,
        query: str,
        output_file: str | None = None,
        *,
        limit: int = 10,
    ) -> list[dict]:
        """Search the web using firecrawl search (no HTTP fallback - requires CLI)."""
        if not self._has_cli:
            log.warning("search_requires_firecrawl_cli", query=query)
            return []

        if output_file is None:
            safe_query = query.replace(" ", "_")[:50]
            output_file = f"{self.output_dir}/search-{safe_query}.json"

        stdout, stderr, returncode = await run_command(
            "firecrawl",
            "search",
            query,
            "--json",
            "--limit",
            str(limit),
            "-o",
            output_file,
            timeout=60,
        )

        if returncode != 0:
            log.error("firecrawl_search_failed", query=query, stderr=stderr[:500])
            return []

        try:
            with open(output_file) as f:
                data = json.load(f)
            return data if isinstance(data, list) else data.get("results", [])
        except (json.JSONDecodeError, FileNotFoundError):
            return []
