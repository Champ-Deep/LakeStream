import asyncio
import time


class RateLimiter:
    """Per-domain rate limiting to avoid blocks."""

    def __init__(self, default_delay_ms: int = 1000):
        self._last_request: dict[str, float] = {}
        self._default_delay = default_delay_ms / 1000.0

    async def wait(self, domain: str, delay_ms: int | None = None) -> None:
        """Wait if needed to respect rate limits for a domain."""
        delay = (delay_ms / 1000.0) if delay_ms else self._default_delay
        last = self._last_request.get(domain, 0)
        elapsed = time.time() - last
        remaining = delay - elapsed

        if remaining > 0:
            await asyncio.sleep(remaining)

        self._last_request[domain] = time.time()

    def reset(self, domain: str) -> None:
        """Reset the rate limit timer for a domain."""
        self._last_request.pop(domain, None)
