from __future__ import annotations

import itertools
import json
import threading
import time
from typing import Any
from urllib.parse import urlparse

import redis.asyncio as redis
import structlog
from playwright.async_api import async_playwright

from src.config.constants import TIER_COSTS
from src.config.settings import get_settings
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier
from src.scraping.fetcher.captcha_detector import detect_captcha

log = structlog.get_logger()

# Module-level round-robin state for proxy pool rotation.
# Persists across fetcher instances within a single worker process.
_pool_cycle: itertools.cycle[str] | None = None
_pool_lock = threading.Lock()


def _next_pool_proxy() -> str | None:
    """Pick next proxy from pool via round-robin. None if pool empty."""
    global _pool_cycle
    settings = get_settings()
    if not settings.proxy_pool_urls:
        return None
    urls = [u.strip() for u in settings.proxy_pool_urls.split(",") if u.strip()]
    if not urls:
        return None
    with _pool_lock:
        if _pool_cycle is None:
            _pool_cycle = itertools.cycle(urls)
        return next(_pool_cycle)


class LakePlaywrightProxyFetcher:
    """Tier 3: Playwright with session persistence + residential proxy.

    Proxy Priority Chain:
    1. Custom proxy (settings.custom_proxy_url)
    2. Bright Data (settings.brightdata_proxy_url)
    3. Smartproxy (settings.smartproxy_url)
    4. No proxy (falls back to PLAYWRIGHT behavior)

    Sessions are stored in Redis with TTL (default 1 hour).
    Cost: $0.0035 per request
    """

    def __init__(self):
        self._redis_client: redis.Redis | None = None

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        """Fetch URL with session persistence + proxy via Playwright browser context.

        Tries each proxy provider in priority order. If a provider fails with a
        connection/timeout error, falls back to the next provider in the chain.
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
                captcha = detect_captcha(html) if html else False
                blocked = http_error or tiny_html

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

        Priority: Pool (round-robin) → Custom → Bright Data → Smartproxy → None
        """
        settings = get_settings()
        chain: list[dict[str, Any]] = []

        # Self-hosted proxy pool (cheapest — just VPS cost)
        pool_url = _next_pool_proxy()
        if pool_url:
            chain.append({"server": pool_url})

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
        if self._redis_client is None:
            settings = get_settings()
            self._redis_client = redis.from_url(settings.redis_url)
        return self._redis_client

    async def _load_session(self, client: redis.Redis, domain: str) -> dict[str, Any] | None:
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
