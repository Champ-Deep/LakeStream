"""Authenticated session manager for LinkedIn/Apollo server-side scraping.

Manages long-lived Playwright browser sessions with:
- Cookie injection from Chrome extension or settings
- Anti-detection (random viewports, user agents, human-like delays)
- Redis-backed session persistence with TTL
- Session lifecycle (create, get, refresh, destroy)
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any

import redis.asyncio as redis
import structlog
from playwright.async_api import BrowserContext, Page

from src.config.settings import get_settings

log = structlog.get_logger()

# Curated user agents — real Chrome on desktop, updated periodically
_UA_BASE = "AppleWebKit/537.36 (KHTML, like Gecko)"
_USER_AGENTS = [
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) {_UA_BASE} Chrome/131.0.0.0 Safari/537.36",
    f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) {_UA_BASE} Chrome/131.0.0.0 Safari/537.36",
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) {_UA_BASE} Chrome/130.0.0.0 Safari/537.36",
    f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) {_UA_BASE} Chrome/130.0.0.0 Safari/537.36",
    f"Mozilla/5.0 (X11; Linux x86_64) {_UA_BASE} Chrome/131.0.0.0 Safari/537.36",
]

_VIEWPORTS = [
    {"width": 1280, "height": 720},
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1920, "height": 1080},
]

SESSION_KEY_PREFIX = "auth_session"
DEFAULT_SESSION_TTL = 7200  # 2 hours (longer than regular sessions)


async def random_delay(min_ms: int = 800, max_ms: int = 3000) -> None:
    """Human-like delay between actions."""
    delay = random.randint(min_ms, max_ms) / 1000
    await asyncio.sleep(delay)


class AuthenticatedSessionManager:
    """Manages authenticated browser sessions for LinkedIn and Apollo."""

    def __init__(self) -> None:
        self._redis: redis.Redis | None = None

    async def _get_redis(self) -> redis.Redis:
        if self._redis is None:
            settings = get_settings()
            self._redis = redis.from_url(settings.redis_url)
        return self._redis

    def _session_key(self, domain: str) -> str:
        return f"{SESSION_KEY_PREFIX}:{domain}"

    async def create_session(
        self,
        domain: str,
        cookies: list[dict[str, Any]],
        *,
        ttl: int = DEFAULT_SESSION_TTL,
    ) -> str:
        """Create an authenticated session from cookies (e.g. from Chrome extension).

        Converts Chrome extension cookie format to Playwright storage_state format,
        then stores in Redis.

        Returns:
            Session key for retrieval.
        """
        # Convert cookies to Playwright storage_state format
        playwright_cookies = []
        for c in cookies:
            pc: dict[str, Any] = {
                "name": c.get("name", ""),
                "value": c.get("value", ""),
                "domain": c.get("domain", domain),
                "path": c.get("path", "/"),
            }
            if "expirationDate" in c:
                pc["expires"] = c["expirationDate"]
            if "sameSite" in c:
                # Playwright expects "Strict", "Lax", or "None"
                ss = c["sameSite"]
                pc["sameSite"] = ss.capitalize() if ss in ("strict", "lax", "none") else "Lax"
            else:
                pc["sameSite"] = "Lax"
            pc["secure"] = c.get("secure", False)
            pc["httpOnly"] = c.get("httpOnly", False)
            playwright_cookies.append(pc)

        storage_state = {
            "cookies": playwright_cookies,
            "origins": [],
        }

        session_data = {
            "storage_state": storage_state,
            "created_at": time.time(),
            "last_used_at": time.time(),
            "request_count": 0,
            "authenticated": True,
            "user_agent": random.choice(_USER_AGENTS),
            "viewport": random.choice(_VIEWPORTS),
        }

        client = await self._get_redis()
        key = self._session_key(domain)
        await client.set(key, json.dumps(session_data), ex=ttl)

        log.info(
            "session_created",
            domain=domain,
            cookie_count=len(playwright_cookies),
            ttl=ttl,
        )
        return key

    async def get_session(self, domain: str) -> dict[str, Any] | None:
        """Load session from Redis. Returns None if expired or missing."""
        client = await self._get_redis()
        data = await client.get(self._session_key(domain))
        if not data:
            return None
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return None

    async def update_session(
        self,
        domain: str,
        storage_state: dict[str, Any],
        request_count_increment: int = 1,
    ) -> None:
        """Update session after a successful page visit."""
        client = await self._get_redis()
        key = self._session_key(domain)
        existing = await self.get_session(domain)
        if not existing:
            return

        existing["storage_state"] = storage_state
        existing["last_used_at"] = time.time()
        existing["request_count"] = existing.get("request_count", 0) + request_count_increment

        # Preserve remaining TTL
        ttl = await client.ttl(key)
        if ttl < 0:
            ttl = DEFAULT_SESSION_TTL

        await client.set(key, json.dumps(existing), ex=ttl)

    async def destroy_session(self, domain: str) -> None:
        """Delete session from Redis."""
        client = await self._get_redis()
        await client.delete(self._session_key(domain))
        log.info("session_destroyed", domain=domain)

    async def create_browser_context(
        self,
        playwright_browser: Any,
        domain: str,
        *,
        proxy: dict[str, str] | None = None,
    ) -> tuple[BrowserContext, dict[str, Any]]:
        """Create a Playwright BrowserContext with anti-detection from session data.

        Returns:
            Tuple of (context, session_data). If no session exists, creates a
            fresh context with randomized fingerprint.
        """
        session = await self.get_session(domain)

        user_agent = session["user_agent"] if session else random.choice(_USER_AGENTS)
        viewport = session["viewport"] if session else random.choice(_VIEWPORTS)
        storage_state = session.get("storage_state") if session else None

        context_options: dict[str, Any] = {
            "user_agent": user_agent,
            "viewport": viewport,
            "locale": "en-US",
            "timezone_id": "America/New_York",
        }
        if storage_state:
            context_options["storage_state"] = storage_state
        if proxy:
            context_options["proxy"] = proxy

        context = await playwright_browser.new_context(**context_options)

        # Stealth: override navigator.webdriver
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        return context, session or {
            "created_at": time.time(),
            "last_used_at": time.time(),
            "request_count": 0,
            "authenticated": bool(storage_state),
            "user_agent": user_agent,
            "viewport": viewport,
        }

    async def navigate_with_stealth(
        self,
        page: Page,
        url: str,
        *,
        timeout: int = 30000,
        wait_after: bool = True,
    ) -> int:
        """Navigate to URL with human-like behavior.

        Returns status code.
        """
        settings = get_settings()
        timeout = timeout or settings.playwright_timeout_ms

        response = await page.goto(url, timeout=timeout)
        status = response.status if response else 0

        # Wait for network idle
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception as e:
            log.debug("session_networkidle_timeout", url=url, error=str(e))

        # Human-like: small scroll to trigger lazy loading
        if wait_after:
            await random_delay(500, 1500)
            await page.evaluate("window.scrollBy(0, 300)")
            await random_delay(300, 800)

        return status

    async def is_authenticated(self, page: Page, domain: str) -> bool:
        """Check if current page shows authenticated state.

        Simple heuristic: look for common login/sign-in buttons. If found,
        we're likely NOT authenticated.
        """
        login_selectors = {
            "linkedin.com": [
                'a[href*="/login"]',
                'button[data-tracking-control-name="guest_homepage-basic_sign-in-button"]',
            ],
            "apollo.io": [
                'a[href="/login"]',
                'a[href*="/sign-in"]',
            ],
        }

        selectors = login_selectors.get(domain, [])
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    return False
            except Exception as e:
                log.debug("auth_check_selector_error", domain=domain, selector=sel, error=str(e))

        return True
