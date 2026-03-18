"""Tier 1 fetcher: Lightpanda — a lightweight CDP headless browser.

Lightpanda (https://github.com/lightpanda-io/browser) is a fast, low-memory
headless browser that exposes a Chrome DevTools Protocol (CDP) server. It is
orders of magnitude lighter than Chromium, making it ideal as a first-pass tier
for pages that don't require heavy JS frameworks.

Connection strategy:
1. Locate the `lightpanda` binary (configurable via LIGHTPANDA_BIN_PATH setting,
   falls back to searching PATH).
2. Spawn a short-lived CDP server on a random free port (lightpanda --serve ...),
3. Connect via playwright.connect_over_cdp() and fetch the page.
4. Tear down the subprocess after the fetch.

Graceful fallback:
- If the binary is not found → blocked=True (escalates to Playwright automatically).
- If CDP connection fails or the page errors → blocked=True (escalates).

This means the three-tier chain works transparently even when Lightpanda is not
installed: it simply always escalates and Playwright handles everything.

Cost: $0.0005 per request (6× cheaper than Playwright $0.003)
"""

import asyncio
import socket
import time
from urllib.parse import urlparse

import structlog
from playwright.async_api import async_playwright

from src.config.constants import TIER_COSTS
from src.config.settings import get_settings
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier

log = structlog.get_logger()


def _find_free_port() -> int:
    """Bind to port 0 and let the OS pick a free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _find_lightpanda_binary(bin_path: str) -> str | None:
    """Return the path to the lightpanda binary, or None if not found.

    Checks (in order):
    1. Explicit path from settings (LIGHTPANDA_BIN_PATH env var).
    2. 'lightpanda' on the system PATH via `which`.
    """
    import shutil

    if bin_path:
        import os
        if os.path.isfile(bin_path) and os.access(bin_path, os.X_OK):
            return bin_path

    return shutil.which("lightpanda")


class LakeLightpandaFetcher:
    """Tier 1: Lightpanda CDP headless browser fetcher.

    Fastest and cheapest tier. Ideal for:
    - Static or lightly dynamic pages
    - Bulk URL discovery (sitemaps, blog index pages)
    - Any page that doesn't require heavy JS frameworks

    Automatically fails over to Playwright (tier 2) when:
    - Lightpanda binary is not installed
    - CDP connection times out or errors
    - Page returns a block/CAPTCHA response
    """

    # How long to wait for the CDP server to become ready (seconds)
    _CDP_READY_TIMEOUT = 8.0
    # How long between port-readiness poll attempts (seconds)
    _CDP_POLL_INTERVAL = 0.15

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        """Fetch URL via Lightpanda CDP server.

        Returns a FetchResult with blocked=True if Lightpanda is unavailable,
        which triggers automatic escalation to the Playwright tier.
        """
        options = options or FetchOptions()
        settings = get_settings()
        start = time.time()

        domain = urlparse(url).netloc
        port = _find_free_port()
        cdp_url = f"ws://127.0.0.1:{port}"

        binary = _find_lightpanda_binary(settings.lightpanda_bin_path)
        if not binary:
            log.debug(
                "lightpanda_binary_not_found",
                url=url,
                hint="Install lightpanda or set LIGHTPANDA_BIN_PATH; escalating to playwright",
            )
            return self._blocked_result(url, start)

        process: asyncio.subprocess.Process | None = None
        try:
            # Launch Lightpanda CDP server
            process = await asyncio.create_subprocess_exec(
                binary,
                "serve",
                "--host", "127.0.0.1",
                "--port", str(port),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )

            # Wait for the CDP port to become reachable
            ready = await self._wait_for_port("127.0.0.1", port, self._CDP_READY_TIMEOUT)
            if not ready:
                log.warning(
                    "lightpanda_cdp_not_ready",
                    url=url,
                    port=port,
                    timeout=self._CDP_READY_TIMEOUT,
                )
                return self._blocked_result(url, start)

            # Connect Playwright to the Lightpanda CDP server
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(cdp_url)

                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await context.new_page()

                timeout_ms = options.timeout or settings.playwright_timeout_ms
                response = await page.goto(url, timeout=timeout_ms)

                html = await page.content()
                status_code = response.status if response else 0

                await browser.close()

            # Block detection
            http_error = status_code in (403, 429, 503)
            tiny_html = len(html) < settings.min_html_bytes
            blocked = http_error or tiny_html

            duration_ms = int((time.time() - start) * 1000)
            log.debug(
                "lightpanda_fetch_ok",
                url=url,
                domain=domain,
                status=status_code,
                html_bytes=len(html),
                blocked=blocked,
                duration_ms=duration_ms,
            )

            return FetchResult(
                url=url,
                status_code=status_code,
                html=html,
                headers={},
                tier_used=ScrapingTier.LIGHTPANDA,
                cost_usd=TIER_COSTS["lightpanda"],
                duration_ms=duration_ms,
                blocked=blocked,
                captcha_detected=False,
            )

        except Exception as exc:
            log.warning(
                "lightpanda_fetch_error",
                url=url,
                domain=domain,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return self._blocked_result(url, start)

        finally:
            if process is not None and process.returncode is None:
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=3.0)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass

    async def _wait_for_port(self, host: str, port: int, timeout: float) -> bool:
        """Poll until the TCP port is open or timeout expires."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port),
                    timeout=self._CDP_POLL_INTERVAL,
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return True
            except Exception:
                await asyncio.sleep(self._CDP_POLL_INTERVAL)
        return False

    def _blocked_result(self, url: str, start: float) -> FetchResult:
        """Return a blocked FetchResult to trigger escalation to next tier."""
        return FetchResult(
            url=url,
            status_code=0,
            html="",
            headers={},
            tier_used=ScrapingTier.LIGHTPANDA,
            cost_usd=TIER_COSTS["lightpanda"],
            duration_ms=int((time.time() - start) * 1000),
            blocked=True,
            captcha_detected=False,
        )
