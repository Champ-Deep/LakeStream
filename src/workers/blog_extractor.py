import re
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

from src.models.scraped_data import BlogUrlMetadata, DataType, ScrapedData
from src.utils.url import extract_domain
from src.workers.base import BaseWorker

# Selectors used to find "next page" links on blog index pages
_NEXT_PAGE_SELECTORS = [
    "a[rel='next']",
    ".next a",
    ".nav-next a",
    "a.next",
    ".pagination a.next",
    ".wp-pagenavi a.nextpostslink",
    "a[aria-label='Next page']",
    "a[aria-label='Next']",
]

# Regex patterns that indicate a URL is a paginated blog index rather than an article
_PAGINATION_PATTERNS = [
    re.compile(r"/page/(\d+)/?$", re.IGNORECASE),      # /blog/page/2
    re.compile(r"[?&]page=(\d+)", re.IGNORECASE),       # /blog?page=2 or /blog?tag=x&page=2
    re.compile(r"[?&]paged=(\d+)", re.IGNORECASE),      # WordPress paged param
    re.compile(r"[?&]p=(\d+)", re.IGNORECASE),          # some CMSes
    re.compile(r"/(\d+)/?$", re.IGNORECASE),             # /blog/2 (numeric pagination)
]

# Maximum paginated index pages to follow per blog landing URL
# 200 pages × ~10 articles = 2000 articles max per blog — well above typical need
_MAX_PAGINATION_PAGES = 200

# Selectors Playwright will scroll/click to trigger infinite-scroll content loading
_LOAD_MORE_SELECTORS = [
    "button:has-text('Load more')",
    "button:has-text('Load More')",
    "button:has-text('Show more')",
    "button:has-text('Show More')",
    "a:has-text('Load more')",
    "a:has-text('Show more')",
    "[data-testid='load-more']",
    ".load-more",
    ".show-more",
    "#load-more",
]

# How many times to trigger infinite scroll per page before giving up
_MAX_SCROLL_ATTEMPTS = 10


class BlogExtractorWorker(BaseWorker):
    """Extracts blog URLs and article links from blog landing pages.

    Follows pagination ('next page' links) so that all articles across all
    pages of a blog index are discovered, not just those visible on page 1.
    """

    _SKIP_EXTENSIONS = frozenset({
        ".pdf", ".doc", ".docx", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    })

    def __init__(self, domain: str, job_id: str, pool: object | None = None, org_id: str | None = None, user_id: str | None = None, tier_override: str | None = None):
        super().__init__(domain=domain, job_id=job_id, pool=pool, org_id=org_id, user_id=user_id, tier_override=tier_override)

    def _filter_article_links(self, links: list[str], source_url: str) -> list[str]:
        """Remove homepage, non-HTML, paginated index, and off-domain links."""
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
            # Skip links that are themselves pagination pages (e.g. /blog/page/2)
            if self._is_pagination_url(link):
                continue
            filtered.append(link)
        return filtered

    def _is_pagination_url(self, url: str) -> bool:
        """Return True if the URL looks like a paginated index page."""
        for pattern in _PAGINATION_PATTERNS:
            if pattern.search(url):
                return True
        return False

    def _extract_next_page_url(self, html_parser, current_url: str) -> str | None:
        """Find the next-page URL from HTML, returning an absolute URL or None."""
        for selector in _NEXT_PAGE_SELECTORS:
            links = html_parser.extract_links(selectors=[selector], base_url=current_url)
            for link in links:
                if link and link != current_url and extract_domain(link) == extract_domain(current_url):
                    return link
        return None

    async def _expand_infinite_scroll(self, page: object, current_url: str) -> str:
        """Attempt to trigger infinite-scroll / load-more to reveal all articles.

        Clicks known "Load more" buttons up to _MAX_SCROLL_ATTEMPTS times,
        scrolling to the bottom after each click. Returns final full-page HTML.

        Falls back to current page HTML on any error (non-fatal).
        """
        try:
            for attempt in range(_MAX_SCROLL_ATTEMPTS):
                # Scroll to bottom to trigger lazy-load
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(800)

                # Try each "load more" selector
                clicked = False
                for selector in _LOAD_MORE_SELECTORS:
                    try:
                        btn = await page.query_selector(selector)
                        if btn and await btn.is_visible():
                            await btn.click()
                            await page.wait_for_timeout(1200)
                            clicked = True
                            self.log.debug(
                                "infinite_scroll_click",
                                url=current_url,
                                selector=selector,
                                attempt=attempt + 1,
                            )
                            break
                    except Exception:
                        continue

                if not clicked:
                    # No load-more button found — nothing more to expand
                    break

            return await page.content()
        except Exception as e:
            self.log.debug("infinite_scroll_error", url=current_url, error=str(e))
            try:
                return await page.content()
            except Exception:
                return ""

    async def _collect_all_article_links(self, landing_url: str) -> tuple[list[str], str | None]:
        """Fetch the landing page and all its paginated pages, collecting article links.

        Handles two pagination strategies:
        1. Traditional next-page links (/blog/page/2, ?page=2, etc.)
        2. Infinite scroll / load-more buttons (JS-triggered content)

        Returns:
            (all_article_links, page_title) — deduplicated article links across all pages.
        """
        from src.scraping.parser.html_parser import HtmlParser

        all_links: list[str] = []
        seen_links: set[str] = set()
        visited_pages: set[str] = set()
        current_url: str | None = landing_url
        page_title: str | None = None
        pages_fetched = 0

        # Detect if the fetcher is Playwright-based so we can do scroll expansion
        is_playwright = self._tier_override is not None and "playwright" in str(self._tier_override).lower()

        while current_url and pages_fetched < _MAX_PAGINATION_PAGES:
            if current_url in visited_pages:
                break
            visited_pages.add(current_url)

            try:
                fetch_result = await self.fetch_page(current_url)
            except Exception as e:
                self.log.error("blog_page_fetch_error", url=current_url, error=str(e))
                break

            if fetch_result.blocked:
                self.log.warning("blog_page_blocked", url=current_url, page=pages_fetched + 1)
                break

            pages_fetched += 1
            html_to_parse = fetch_result.html
            parser = HtmlParser(html_to_parse, current_url)

            # Capture title from the first (landing) page
            if page_title is None:
                page_title = parser.extract_title()

            # Extract article links from this page
            raw_links = parser.extract_links(
                selectors=[
                    "article a",
                    "h2 a",
                    "h3 a",
                    ".post-title a",
                    ".entry-title a",
                    "a[rel='bookmark']",
                    ".blog-post a",
                    ".post-card a",
                    ".post-item a",
                    ".article-card a",
                    ".article-item a",
                ],
                base_url=current_url,
            )
            filtered = self._filter_article_links(raw_links, current_url)
            links_before_scroll = len(filtered)

            # If no next-page link is found AND this is the first page, attempt
            # infinite-scroll expansion via a direct Playwright page object.
            # We check for next-page first — if it exists, traditional pagination
            # will handle all pages and scroll expansion is unnecessary.
            next_url = self._extract_next_page_url(parser, current_url)
            if not next_url and pages_fetched == 1:
                try:
                    from playwright.async_api import async_playwright
                    from src.config.settings import get_settings
                    settings = get_settings()

                    async with async_playwright() as p:
                        browser = await p.chromium.launch(headless=settings.playwright_headless)
                        context = await browser.new_context()
                        pw_page = await context.new_page()
                        await pw_page.goto(current_url, timeout=settings.playwright_timeout_ms)
                        expanded_html = await self._expand_infinite_scroll(pw_page, current_url)
                        await browser.close()

                    if expanded_html:
                        scroll_parser = HtmlParser(expanded_html, current_url)
                        scroll_links = scroll_parser.extract_links(
                            selectors=[
                                "article a", "h2 a", "h3 a",
                                ".post-title a", ".entry-title a",
                                "a[rel='bookmark']", ".blog-post a",
                                ".post-card a", ".post-item a",
                                ".article-card a", ".article-item a",
                            ],
                            base_url=current_url,
                        )
                        filtered = self._filter_article_links(scroll_links, current_url)
                        self.log.info(
                            "infinite_scroll_expanded",
                            url=current_url,
                            before=links_before_scroll,
                            after=len(filtered),
                        )
                except Exception as e:
                    self.log.debug("infinite_scroll_playwright_error", url=current_url, error=str(e))

            for link in filtered:
                if link not in seen_links:
                    seen_links.add(link)
                    all_links.append(link)

            self.log.debug(
                "blog_page_scraped",
                url=current_url,
                page=pages_fetched,
                links_found=len(filtered),
                total_so_far=len(all_links),
            )

            # Follow traditional pagination
            if next_url and next_url not in visited_pages:
                current_url = next_url
            else:
                break

        self.log.info(
            "blog_pagination_complete",
            landing_url=landing_url,
            pages_fetched=pages_fetched,
            total_articles=len(all_links),
        )
        return all_links, page_title

    async def execute(self, urls: list[str]) -> list[ScrapedData]:
        if not urls:
            self.log.info("no_blog_urls_to_process")
            return []

        self.log.info("extracting_blogs", url_count=len(urls))
        results: list[ScrapedData] = []

        for url in urls:
            try:
                article_links, page_title = await self._collect_all_article_links(url)

                metadata = BlogUrlMetadata(
                    blog_landing_url=url,
                    article_urls=article_links,
                    total_articles=len(article_links),
                )

                record = {
                    "job_id": UUID(self.job_id),
                    "domain": self.domain,
                    "data_type": DataType.BLOG_URL,
                    "url": url,
                    "title": page_title,
                    "metadata": metadata.model_dump(),
                }
                await self.export_results([record])

                results.append(
                    ScrapedData(
                        id=UUID(int=0),
                        job_id=UUID(self.job_id),
                        domain=self.domain,
                        data_type=DataType.BLOG_URL,
                        url=url,
                        title=page_title,
                        metadata=metadata.model_dump(),
                        scraped_at=datetime.now(UTC),
                    )
                )

            except Exception as e:
                self.log.error("blog_extract_error", url=url, error=str(e))

        self.log.info("blogs_extracted", count=len(results))
        return results
