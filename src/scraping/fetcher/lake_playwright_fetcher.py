import time
from urllib.parse import urlparse

import structlog
from playwright.async_api import async_playwright

from src.config.constants import TIER_COSTS
from src.config.settings import get_settings
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier
from src.scraping.fetcher.base import BaseFetcher

log = structlog.get_logger()


class LakePlaywrightFetcher(BaseFetcher):
    """Tier 2.5: Playwright-based fetcher with Redis-backed session persistence.

    This fetcher uses Playwright's Python API directly (not CLI) to enable:
    - Cookie/session persistence across requests to same domain
    - Authenticated scraping for sites like LinkedIn
    - Reduced browser startup overhead through session reuse

    Sessions are stored in Redis with TTL (default 1 hour) using the key format:
    `playwright_session:{domain}`

    Cost: $0.003 per request (between HEADLESS_BROWSER $0.002 and HEADLESS_PROXY $0.004)
    """

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        """Fetch URL with session persistence via Playwright browser context.

        Workflow:
        1. Extract domain from URL
        2. Try load existing session from Redis
        3. Launch Playwright browser
        4. Create context (with storage_state if session exists)
        5. Navigate to URL
        6. Extract HTML + status code
        7. Save updated storage_state to Redis
        8. Return FetchResult

        Args:
            url: Target URL to fetch
            options: Fetch options (timeout configurable)

        Returns:
            FetchResult with HTML, status code, cost, duration, and block detection
        """
        options = options or FetchOptions()
        settings = get_settings()
        start = time.time()

        domain = urlparse(url).netloc

        # PDF shortcut — download binary via httpx (no browser needed)
        if url.lower().endswith(".pdf"):
            return await self._fetch_pdf(url, start)

        try:
            # Get Redis client (lazy initialization)
            redis_client = await self._get_redis_client()

            # Try load existing session
            session_data = await self._load_session(redis_client, domain)
            storage_state = session_data.get("storage_state") if session_data else None

            # Launch Playwright browser
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=settings.playwright_headless)
                context = None
                page = None

                try:
                    # Create context (with session if exists)
                    if storage_state:
                        context = await browser.new_context(storage_state=storage_state)
                        log.debug("playwright_session_loaded", domain=domain, url=url)
                    else:
                        context = await browser.new_context()
                        log.debug("playwright_fresh_context", domain=domain, url=url)

                    # Navigate to URL
                    page = await context.new_page()
                    timeout = options.timeout if options.timeout is not None else settings.playwright_timeout_ms
                    response = await page.goto(url, timeout=timeout)

                    # Wait for network idle (ensures JS content is loaded for SPAs)
                    try:
                        await page.wait_for_load_state('networkidle', timeout=10000)
                    except Exception as e:
                        log.debug(
                            "playwright_networkidle_timeout",
                            url=url, domain=domain, error=str(e),
                        )

                    # Extract content
                    html = await page.content()
                    status_code = response.status if response else 0

                    # Save updated session (cookies may have changed)
                    updated_storage_state = await context.storage_state()
                    await self._save_session(
                        redis_client,
                        domain,
                        updated_storage_state,
                        {
                            "last_used_at": time.time(),
                            "request_count": (session_data.get("request_count", 0) + 1)
                            if session_data
                            else 1,
                            "authenticated": (
                                session_data.get("authenticated", False)
                                if session_data else False
                            ),
                        },
                    )
                finally:
                    if page:
                        await page.close()
                    if context:
                        await context.close()
                    await browser.close()

            # Block detection
            blocked, captcha = self.is_blocked(status_code, html)

        except Exception as exc:
            log.warning(
                "lake_playwright_fetcher_error",
                url=url,
                domain=domain,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            html = ""
            status_code = 0
            blocked = True
            captcha = False  # no HTML to scan on error

        duration_ms = int((time.time() - start) * 1000)

        return FetchResult(
            url=url,
            status_code=status_code,
            html=html,
            headers={},
            tier_used=ScrapingTier.PLAYWRIGHT,
            cost_usd=TIER_COSTS["playwright"],
            duration_ms=duration_ms,
            blocked=blocked,
            captcha_detected=captcha,
        )

    async def _fetch_pdf(self, url: str, start: float) -> FetchResult:
        """Download PDF via httpx (no browser needed)."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(url)
                duration_ms = int((time.time() - start) * 1000)

                return FetchResult(
                    url=url,
                    status_code=resp.status_code,
                    html="",
                    headers=dict(resp.headers),
                    tier_used=ScrapingTier.PLAYWRIGHT,
                    cost_usd=TIER_COSTS["playwright"],
                    duration_ms=duration_ms,
                    blocked=resp.status_code in (403, 429, 503),
                    captcha_detected=False,
                    content_bytes=resp.content,
                    content_type=resp.headers.get(
                        "content-type", "application/pdf"
                    ),
                )
        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            log.warning("pdf_download_error", url=url, error=str(exc))
            return FetchResult(
                url=url,
                status_code=0,
                html="",
                headers={},
                tier_used=ScrapingTier.PLAYWRIGHT,
                cost_usd=TIER_COSTS["playwright"],
                duration_ms=duration_ms,
                blocked=True,
                captcha_detected=False,
            )
