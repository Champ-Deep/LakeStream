"""Shared base class for all fetcher tiers.

Every fetcher (LightPanda, Playwright, Playwright+Proxy) implements the same
``fetch(url, options) -> FetchResult`` contract consumed by
``src.scraping.fetcher.factory.create_fetcher`` and
``src.workers.base.BaseWorker.fetch_page``. This module centralizes the
logic that was previously copy-pasted across those fetcher implementations:

- Redis-backed session persistence (``_get_redis_client``, ``_load_session``,
  ``_save_session``), used by the Playwright-based fetchers to reuse cookies
  across requests to the same domain.
- Block detection (``is_blocked``), which inspects the HTTP status code,
  HTML length, and CAPTCHA markers to decide whether a fetch was blocked.

Retry and rate-limiting are intentionally NOT part of this base class yet —
they currently live one layer up, in ``BaseWorker.fetch_page`` (via
``retry_async`` and ``RateLimiter``). Moving that logic into ``fetch()``
itself would be a larger, riskier change (it interacts with the tier
escalation logic in ``BaseWorker``) and is left for a follow-up.
"""

from __future__ import annotations

import abc
import json
import time
from typing import Any

import redis.asyncio as redis
import structlog

from src.config.settings import get_settings
from src.models.scraping import FetchOptions, FetchResult
from src.scraping.fetcher.captcha_detector import detect_captcha

log = structlog.get_logger()


class BaseFetcher(abc.ABC):
    """Common interface and shared helpers for all scraping-tier fetchers."""

    def __init__(self) -> None:
        self._redis_client: redis.Redis | None = None

    @abc.abstractmethod
    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        """Fetch ``url`` and return a :class:`FetchResult`.

        Implementations must never raise for ordinary fetch failures (network
        errors, blocks, timeouts) — those are reported via
        ``FetchResult.blocked`` / ``status_code`` so callers (retry logic,
        tier escalation) can inspect them uniformly.
        """
        ...

    # -- Block detection -------------------------------------------------

    @staticmethod
    def is_blocked(status_code: int, html: str) -> tuple[bool, bool]:
        """Detect whether a response looks blocked/challenged.

        Returns a ``(blocked, captcha_detected)`` tuple, matching the checks
        previously duplicated across every fetcher: HTTP status in
        (403, 429, 503), suspiciously small HTML payloads, or known CAPTCHA
        markers in the HTML.
        """
        settings = get_settings()
        http_error = status_code in (403, 429, 503)
        tiny_html = len(html) < settings.min_html_bytes
        captcha = detect_captcha(html) if html else False
        blocked = http_error or tiny_html
        return blocked, captcha

    # -- Redis-backed session persistence --------------------------------
    #
    # Shared by the Playwright-based fetchers, which persist browser
    # storage_state (cookies, localStorage) per-domain so that subsequent
    # fetches can resume an authenticated/warmed-up session.

    async def _get_redis_client(self) -> redis.Redis:
        """Lazy Redis client initialization (cached after first call)."""
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
            metadata: Additional metadata (created_at, last_used_at, request_count,
                authenticated, and optionally proxy_used)
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
        if "proxy_used" in metadata:
            session_data["proxy_used"] = metadata.get("proxy_used")

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
                **(
                    {"proxy_used": session_data["proxy_used"]}
                    if "proxy_used" in session_data
                    else {}
                ),
            )
        except Exception as exc:
            log.warning(
                "playwright_session_save_error",
                domain=domain,
                error=str(exc),
                error_type=type(exc).__name__,
            )
