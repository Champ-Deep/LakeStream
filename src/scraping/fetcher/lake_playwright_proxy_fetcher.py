from __future__ import annotations

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
from src.scraping.fetcher.captcha_detector import detect_captcha
from src.services.proxy_health import (
    ProxyHealthTracker,
    get_region_headers,
)

log = structlog.get_logger()

# Lazy-initialized health tracker (shared across fetcher instances in a worker)
_health_tracker: ProxyHealthTracker | None = None


def _get_health_tracker() -> ProxyHealthTracker:
    global _health_tracker
    if _health_tracker is None:
        _health_tracker = ProxyHealthTracker()
    return _health_tracker


def _get_pool_proxies() -> list[dict[str, str]]:
    """Parse proxy pool from settings. Supports both formats:
    - proxy_pool_config (JSON with region tags): [{"url":"...","region":"us"}]
    - proxy_pool_urls (comma-separated, no regions): "http://vps1:3128,http://vps2:3128"
    """
    settings = get_settings()

    # Prefer structured config
    if settings.proxy_pool_config:
        try:
            pool = json.loads(settings.proxy_pool_config)
            if isinstance(pool, list):
                return pool
        except (json.JSONDecodeError, TypeError):
            log.warning("invalid_proxy_pool_config")

    # Fallback to comma-separated URLs
    if settings.proxy_pool_urls:
        urls = [u.strip() for u in settings.proxy_pool_urls.split(",") if u.strip()]
        return [{"url": u} for u in urls]

    return []


class LakePlaywrightProxyFetcher:
    """Tier 3: Playwright with session persistence + residential proxy.

    Proxy Priority Chain (with health-aware pool selection):
    1. Pool proxy (health-weighted, region-filtered)
    2. Custom proxy (settings.custom_proxy_url)
    3. Bright Data (settings.brightdata_proxy_url)
    4. Smartproxy (settings.smartproxy_url)
    5. No proxy (falls back to PLAYWRIGHT behavior)

    Sessions are stored in Redis with TTL (default 1 hour).
    Cost: $0.0035 per request
    """

    def __init__(self):
        self._redis_client: redis.Redis | None = None

    async def fetch(
        self, url: str, options: FetchOptions | None = None,
    ) -> FetchResult:
        """Fetch URL with session persistence + proxy via Playwright.

        Tries each proxy provider in priority order. If a provider fails
        with a connection/timeout error, falls back to the next in chain.
        """
        options = options or FetchOptions()
        settings = get_settings()
        start = time.time()

        domain = urlparse(url).netloc
        region = options.region

        # Build proxy chain for failover (may be empty)
        proxy_chain = await self._get_proxy_chain(region=region)
        # Always include a None entry as last resort (no proxy)
        proxy_configs: list[dict[str, Any] | None] = [*proxy_chain, None]

        # Region-specific headers
        region_headers = get_region_headers(region)

        html = ""
        status_code = 0
        blocked = True
        captcha = False
        proxy_config: dict[str, Any] | None = None
        used_proxy_url: str | None = None

        for i, proxy_config in enumerate(proxy_configs):
            try:
                redis_client = await self._get_redis_client()
                session_data = await self._load_session(redis_client, domain)
                storage_state = (
                    session_data.get("storage_state") if session_data else None
                )

                async with async_playwright() as p:
                    browser = await p.chromium.launch(
                        headless=settings.playwright_headless,
                    )

                    context_options: dict[str, Any] = {}
                    if storage_state:
                        context_options["storage_state"] = storage_state
                    if proxy_config:
                        context_options["proxy"] = proxy_config

                    # Apply region headers to context
                    if region_headers:
                        context_options.setdefault(
                            "extra_http_headers", {},
                        ).update(region_headers)

                    context = await browser.new_context(**context_options)

                    used_proxy_url = (
                        proxy_config.get("server") if proxy_config else None
                    )
                    log.debug(
                        "playwright_proxy_attempt",
                        domain=domain,
                        url=url,
                        proxy=used_proxy_url,
                        region=region,
                        attempt=i + 1,
                        total_providers=len(proxy_configs),
                    )

                    page = await context.new_page()
                    timeout = options.timeout or settings.playwright_timeout_ms
                    response = await page.goto(url, timeout=timeout)

                    try:
                        await page.wait_for_load_state(
                            "networkidle", timeout=10000,
                        )
                    except Exception:
                        pass  # Non-fatal

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
                            "request_count": (
                                session_data.get("request_count", 0) + 1
                            )
                            if session_data
                            else 1,
                            "authenticated": (
                                session_data.get("authenticated", False)
                                if session_data
                                else False
                            ),
                            "proxy_used": used_proxy_url,
                        },
                    )

                    await browser.close()

                # Block detection
                http_error = status_code in (403, 429, 503)
                tiny_html = len(html) < settings.min_html_bytes
                captcha = detect_captcha(html) if html else False
                blocked = http_error or tiny_html

                # Record proxy health
                fetch_ms = int((time.time() - start) * 1000)
                if used_proxy_url:
                    tracker = _get_health_tracker()
                    if blocked:
                        await tracker.record_failure(used_proxy_url)
                    else:
                        await tracker.record_success(
                            used_proxy_url, fetch_ms,
                        )

                # Success (even if blocked) — don't failover for HTTP blocks
                break

            except Exception as exc:
                proxy_server = (
                    proxy_config.get("server") if proxy_config else None
                )

                # Record connection failure for health tracking
                if proxy_server:
                    tracker = _get_health_tracker()
                    await tracker.record_failure(proxy_server)

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

    async def _get_proxy_chain(
        self, region: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get all available proxy configs in priority order.

        Priority: Pool (health-weighted) -> Custom -> BrightData -> Smartproxy
        """
        settings = get_settings()
        chain: list[dict[str, Any]] = []

        # Self-hosted proxy pool (health-aware selection)
        pool_proxies = _get_pool_proxies()
        if pool_proxies:
            tracker = _get_health_tracker()
            picked = await tracker.pick_proxy(pool_proxies, region=region)
            if picked:
                url = picked.get("url") or picked.get("server", "")
                chain.append({"server": url})

        if settings.custom_proxy_url:
            proxy_config: dict[str, Any] = {
                "server": settings.custom_proxy_url,
            }
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

    async def _load_session(
        self, client: redis.Redis, domain: str,
    ) -> dict[str, Any] | None:
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
