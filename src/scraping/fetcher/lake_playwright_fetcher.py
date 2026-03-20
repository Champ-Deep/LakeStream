import json
import time
from typing import Any
from urllib.parse import urlparse

import redis.asyncio as redis
import structlog
from playwright.async_api import async_playwright

from src.config.constants import TIER_COSTS
from src.config.settings import get_settings
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier
from src.scraping.fetcher.lake_lightpanda_fetcher import _detect_captcha

log = structlog.get_logger()


class LakePlaywrightFetcher:
    """Tier 2.5: Playwright-based fetcher with Redis-backed session persistence.

    This fetcher uses Playwright's Python API directly (not CLI) to enable:
    - Cookie/session persistence across requests to same domain
    - Authenticated scraping for sites like LinkedIn
    - Reduced browser startup overhead through session reuse

    Sessions are stored in Redis with TTL (default 1 hour) using the key format:
    `playwright_session:{domain}`

    Cost: $0.003 per request (between HEADLESS_BROWSER $0.002 and HEADLESS_PROXY $0.004)
    """

    def __init__(self):
        self._redis_client: redis.Redis | None = None

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

        try:
            # Get Redis client (lazy initialization)
            redis_client = await self._get_redis_client()

            # Try load existing session
            session_data = await self._load_session(redis_client, domain)
            storage_state = session_data.get("storage_state") if session_data else None

            # Launch Playwright browser
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=settings.playwright_headless)

                # Create context (with session if exists)
                if storage_state:
                    context = await browser.new_context(storage_state=storage_state)
                    log.debug("playwright_session_loaded", domain=domain, url=url)
                else:
                    context = await browser.new_context()
                    log.debug("playwright_fresh_context", domain=domain, url=url)

                # Navigate to URL
                page = await context.new_page()
                response = await page.goto(url, timeout=options.timeout or settings.playwright_timeout_ms)

                # Wait for network to be idle (ensures JS content is loaded)
                # This is critical for SPAs (React, Vue, Angular) that load content after page load
                try:
                    await page.wait_for_load_state('networkidle', timeout=10000)  # 10s max
                    log.debug("playwright_networkidle_complete", url=url, domain=domain)
                except Exception as e:
                    # If timeout, continue anyway (better partial content than nothing)
                    log.debug("playwright_networkidle_timeout", url=url, domain=domain, error=str(e))

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
                        "authenticated": session_data.get("authenticated", False) if session_data else False,
                    },
                )

                await browser.close()

            # Block detection
            http_error = status_code in (403, 429, 503)
            tiny_html = len(html) < settings.min_html_bytes
            captcha = _detect_captcha(html) if html else False
            blocked = http_error or tiny_html

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

    async def _get_redis_client(self) -> redis.Redis:
        """Lazy Redis client initialization.

        Returns:
            Redis client instance (cached after first call)
        """
        if self._redis_client is None:
            settings = get_settings()
            self._redis_client = redis.from_url(settings.redis_url)
        return self._redis_client

    async def _load_session(self, client: redis.Redis, domain: str) -> dict[str, Any] | None:
        """Load session from Redis.

        Args:
            client: Redis client
            domain: Domain to load session for (e.g., "linkedin.com")

        Returns:
            Session data dict with storage_state and metadata, or None if not found
        """
        key = f"playwright_session:{domain}"
        try:
            data = await client.get(key)
            if data:
                return json.loads(data)
        except Exception as exc:
            log.warning(
                "playwright_session_load_error",
                domain=domain,
                error=str(exc),
                error_type=type(exc).__name__,
            )
        return None

    async def _save_session(
        self,
        client: redis.Redis,
        domain: str,
        storage_state: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        """Save session to Redis with TTL.

        Args:
            client: Redis client
            domain: Domain to save session for (e.g., "linkedin.com")
            storage_state: Playwright storage state (cookies, localStorage, etc.)
            metadata: Additional metadata (created_at, last_used_at, request_count, authenticated)
        """
        settings = get_settings()
        key = f"playwright_session:{domain}"

        session_data = {
            "storage_state": storage_state,
            "created_at": metadata.get("created_at", time.time()),
            "last_used_at": metadata.get("last_used_at", time.time()),
            "request_count": metadata.get("request_count", 1),
            "authenticated": metadata.get("authenticated", False),
        }

        try:
            await client.set(
                key,
                json.dumps(session_data),
                ex=settings.playwright_session_ttl_seconds,
            )
            log.debug(
                "playwright_session_saved",
                domain=domain,
                ttl=settings.playwright_session_ttl_seconds,
                request_count=session_data["request_count"],
            )
        except Exception as exc:
            log.warning(
                "playwright_session_save_error",
                domain=domain,
                error=str(exc),
                error_type=type(exc).__name__,
            )
