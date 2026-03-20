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

        Tries each proxy provider in priority order. If a provider fails with a
        connection/timeout error, falls back to the next provider in the chain.

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

        # Build proxy chain for failover (may be empty)
        proxy_chain = self._get_proxy_chain()
        # Always include a None entry as last resort (no proxy)
        proxy_configs: list[dict[str, Any] | None] = [*proxy_chain, None]

        html = ""
        status_code = 0
        blocked = True
        captcha = False
        proxy_config: dict[str, Any] | None = None

        for i, proxy_config in enumerate(proxy_configs):
            try:
                redis_client = await self._get_redis_client()
                session_data = await self._load_session(redis_client, domain)
                storage_state = session_data.get("storage_state") if session_data else None

                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=settings.playwright_headless)

                    context_options: dict[str, Any] = {}
                    if storage_state:
                        context_options["storage_state"] = storage_state
                    if proxy_config:
                        context_options["proxy"] = proxy_config

                    context = await browser.new_context(**context_options)

                    log.debug(
                        "playwright_proxy_attempt",
                        domain=domain,
                        url=url,
                        proxy=proxy_config.get("server") if proxy_config else None,
                        attempt=i + 1,
                        total_providers=len(proxy_configs),
                    )

                    page = await context.new_page()
                    timeout = options.timeout or settings.playwright_timeout_ms
                    response = await page.goto(url, timeout=timeout)

                    try:
                        await page.wait_for_load_state('networkidle', timeout=10000)
                    except Exception:
                        pass  # Non-fatal: page may still have content

                    html = await page.content()
                    status_code = response.status if response else 0

                    # Save updated session
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
                            "proxy_used": (
                                proxy_config.get("server") if proxy_config else None
                            ),
                        },
                    )

                    await browser.close()

                # Block detection
                http_error = status_code in (403, 429, 503)
                tiny_html = len(html) < settings.min_html_bytes
                blocked = http_error or tiny_html
                captcha = False

                # Success (even if blocked by site) — don't failover for HTTP blocks,
                # only for connection-level failures
                break

            except Exception as exc:
                proxy_server = proxy_config.get("server") if proxy_config else None
                remaining = len(proxy_configs) - i - 1
                if remaining > 0:
                    log.warning(
                        "proxy_failover",
                        url=url,
                        domain=domain,
                        failed_proxy=proxy_server,
                        remaining_providers=remaining,
                        error=str(exc),
                    )
                    continue  # Try next provider
                else:
                    log.warning(
                        "lake_playwright_proxy_fetcher_error",
                        url=url,
                        domain=domain,
                        proxy=proxy_server,
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

    def _get_proxy_chain(self) -> list[dict[str, Any]]:
        """Get all available proxy configs in priority order for failover.

        Priority:
        1. Custom proxy (custom_proxy_url with optional auth)
        2. Bright Data (brightdata_proxy_url)
        3. Smartproxy (smartproxy_url)

        Returns:
            List of proxy config dicts for Playwright context (may be empty)
        """
        settings = get_settings()
        chain: list[dict[str, Any]] = []

        if settings.custom_proxy_url:
            proxy_config: dict[str, Any] = {"server": settings.custom_proxy_url}
            if settings.custom_proxy_username and settings.custom_proxy_password:
                proxy_config["username"] = settings.custom_proxy_username
                proxy_config["password"] = settings.custom_proxy_password
            chain.append(proxy_config)

        if settings.brightdata_proxy_url:
            chain.append({"server": settings.brightdata_proxy_url})

        if settings.smartproxy_url:
            chain.append({"server": settings.smartproxy_url})

        return chain

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
