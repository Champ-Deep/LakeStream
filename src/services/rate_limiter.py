import asyncio
import time


class RateLimiter:
    """Per-domain rate limiting with adaptive delay based on server responses."""

    def __init__(self, default_delay_ms: int = 1000, max_delay_ms: int = 30000):
        self._last_request: dict[str, float] = {}
        self._default_delay = default_delay_ms / 1000.0
        self._max_delay = max_delay_ms / 1000.0
        self._current_delay: dict[str, float] = {}

    async def wait(self, domain: str, delay_ms: int | None = None) -> None:
        """Wait if needed to respect rate limits for a domain."""
        delay = (
            (delay_ms / 1000.0)
            if delay_ms
            else self._current_delay.get(domain, self._default_delay)
        )
        last = self._last_request.get(domain, 0)
        elapsed = time.time() - last
        remaining = delay - elapsed

        if remaining > 0:
            await asyncio.sleep(remaining)

        self._last_request[domain] = time.time()

    def report_result(self, domain: str, status_code: int) -> None:
        """Adjust delay based on server response.

        429/503 doubles delay, success decays toward default.
        """
        current = self._current_delay.get(domain, self._default_delay)

        if status_code in (429, 503):
            new_delay = min(current * 2, self._max_delay)
        elif status_code == 200:
            new_delay = current * 0.9
            if new_delay < self._default_delay:
                new_delay = self._default_delay
        else:
            return

        self._current_delay[domain] = new_delay

    def reset(self, domain: str) -> None:
        """Reset the rate limit timer and adaptive delay for a domain."""
        self._last_request.pop(domain, None)
        self._current_delay.pop(domain, None)
