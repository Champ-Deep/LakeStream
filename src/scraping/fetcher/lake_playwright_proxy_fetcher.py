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

log = structlog.get_logger()


class LakePlaywrightProxyFetcher:
    """Tier 3: Playwright with session persistence + residential proxy.

    This fetcher combines the best of both worlds:
    - Session persistence (cookies, localStorage) from PLAYWRIGHT tier
    - Residential proxy rotation from HEADLESS_PROXY tier
    - Custom proxy support (bring your own proxy)

    Proxy Priority Chain:
    1. Custom proxy (settings.custom_proxy_url)
    2. Bright Data (settings.brightdata_proxy_url)
    3. Smartproxy (settings.smartproxy_url)
    4. No proxy (falls back to PLAYWRIGHT behavior)

    Sessions are stored in Redis with TTL (default 1 hour) using the key format:
    `playwright_session:{domain}`

    Cost: $0.0035 per request (12.5% cheaper than HEADLESS_PROXY $0.004)
    """

    def __init__(self):
        self._redis_client: redis.Redis | None = None

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        """Fetch URL with session persistence + proxy via Playwright browser context.

        Workflow:
        1. Extract domain from URL
        2. Determine proxy configuration (priority chain)
        3. Try load existing session from Redis
        4. Launch Playwright browser
        5. Create context with storage_state + proxy
        6. Navigate to URL
        7. Extract HTML + status code
        8. Save updated storage_state to Redis
        9. Return FetchResult

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

        # Get proxy configuration
        proxy_config = self._get_proxy_config()

        try:
            # Get Redis client (lazy initialization)
            redis_client = await self._get_redis_client()

            # Try load existing session
            session_data = await self._load_session(redis_client, domain)
            storage_state = session_data.get("storage_state") if session_data else None

            # Launch Playwright browser
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=settings.playwright_headless)

                # Create context with session + proxy
                context_options: dict[str, Any] = {}
                if storage_state:
                    context_options["storage_state"] = storage_state
                if proxy_config:
                    context_options["proxy"] = proxy_config

                context = await browser.new_context(**context_options)

                # Log session and proxy status
                if storage_state and proxy_config:
                    log.debug(
                        "playwright_proxy_session_loaded",
                        domain=domain,
                        url=url,
                        proxy=proxy_config.get("server"),
                    )
                elif storage_state:
                    log.debug("playwright_proxy_session_no_proxy", domain=domain, url=url)
                elif proxy_config:
                    log.debug(
                        "playwright_proxy_fresh_context",
                        domain=domain,
                        url=url,
                        proxy=proxy_config.get("server"),
                    )
                else:
                    log.debug("playwright_proxy_no_session_no_proxy", domain=domain, url=url)

                # Navigate to URL
                page = await context.new_page()
                timeout = options.timeout or settings.playwright_timeout_ms
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
                        "proxy_used": proxy_config.get("server") if proxy_config else None,
                    },
                )

                await browser.close()

            # Block detection (same logic as other fetchers)
            http_error = status_code in (403, 429, 503)
            tiny_html = len(html) < settings.min_html_bytes
            blocked = http_error or tiny_html
            captcha = False  # Currently disabled (caused false positives)

        except Exception as exc:
            log.warning(
                "lake_playwright_proxy_fetcher_error",
                url=url,
                domain=domain,
                proxy=proxy_config.get("server") if proxy_config else None,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            html = ""
            status_code = 0
            blocked = True
            captcha = False

        duration_ms = int((time.time() - start) * 1000)

        return FetchResult(
            url=url,
            status_code=status_code,
            html=html,
            headers={},
            tier_used=ScrapingTier.PLAYWRIGHT_PROXY,
            cost_usd=TIER_COSTS["playwright_proxy"],
            duration_ms=duration_ms,
            blocked=blocked,
            captcha_detected=captcha,
        )

    def _get_proxy_config(self) -> dict[str, Any] | None:
        """Get proxy configuration with priority chain.

        Priority:
        1. Custom proxy (custom_proxy_url with optional auth)
        2. Bright Data (brightdata_proxy_url)
        3. Smartproxy (smartproxy_url)
        4. None (no proxy available)

        Returns:
            Proxy config dict for Playwright context, or None if no proxy configured
        """
        settings = get_settings()

        # Priority 1: Custom proxy
        if settings.custom_proxy_url:
            proxy_config: dict[str, Any] = {"server": settings.custom_proxy_url}
            if settings.custom_proxy_username and settings.custom_proxy_password:
                proxy_config["username"] = settings.custom_proxy_username
                proxy_config["password"] = settings.custom_proxy_password
            log.debug("proxy_priority_custom", server=settings.custom_proxy_url)
            return proxy_config

        # Priority 2: Bright Data
        if settings.brightdata_proxy_url:
            log.debug("proxy_priority_brightdata", server=settings.brightdata_proxy_url)
            return {"server": settings.brightdata_proxy_url}

        # Priority 3: Smartproxy
        if settings.smartproxy_url:
            log.debug("proxy_priority_smartproxy", server=settings.smartproxy_url)
            return {"server": settings.smartproxy_url}

        # No proxy available
        log.debug("proxy_priority_none")
        return None

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
            metadata: Additional metadata (created_at, last_used_at,
                request_count, authenticated, proxy_used)
        """
        settings = get_settings()
        key = f"playwright_session:{domain}"

        session_data = {
            "storage_state": storage_state,
            "created_at": metadata.get("created_at", time.time()),
            "last_used_at": metadata.get("last_used_at", time.time()),
            "request_count": metadata.get("request_count", 1),
            "authenticated": metadata.get("authenticated", False),
            "proxy_used": metadata.get("proxy_used"),
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
                proxy_used=session_data["proxy_used"],
            )
        except Exception as exc:
            log.warning(
                "playwright_session_save_error",
                domain=domain,
                error=str(exc),
                error_type=type(exc).__name__,
            )
