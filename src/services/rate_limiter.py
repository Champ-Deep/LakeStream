import asyncio
import fnmatch
import time

import structlog

log = structlog.get_logger()

# Domain-specific rate limits (milliseconds between requests)
# No baseline delay — rely on adaptive backoff for 429/503
DOMAIN_RATE_LIMITS = {
    "linkedin.com": 3000,   # 3 seconds (conservative — LinkedIn bans aggressively)
    "*.linkedin.com": 3000,
    "default": 0,            # No delay — back off adaptively on 429/503
}


class RateLimiter:
    """Per-domain rate limiting with adaptive delay based on server responses.

    No baseline delay — maximum throughput by default. Backs off adaptively
    on 429/503 (doubles delay up to 30s). LinkedIn kept at 3s to avoid bans.
    """

    def __init__(self, default_delay_ms: int = 0, max_delay_ms: int = 30000):
        self._last_request: dict[str, float] = {}
        self._default_delay = default_delay_ms / 1000.0
        self._max_delay = max_delay_ms / 1000.0
        self._current_delay: dict[str, float] = {}

    def get_rate_limit(self, domain: str) -> int:
        """Return domain-specific rate limit in milliseconds.

        Uses pattern matching (fnmatch) to match against DOMAIN_RATE_LIMITS.
        Falls back to default if no pattern matches.

        Args:
            domain: Domain name (e.g., "linkedin.com", "blog.hubspot.com")

        Returns:
            Rate limit in milliseconds
        """
        # Try exact match first
        if domain in DOMAIN_RATE_LIMITS:
            return DOMAIN_RATE_LIMITS[domain]

        # Try pattern matching
        for pattern, limit in DOMAIN_RATE_LIMITS.items():
            if pattern != "default" and fnmatch.fnmatch(domain, pattern):
                log.debug("rate_limit_matched", domain=domain, pattern=pattern, limit_ms=limit)
                return limit

        # Fall back to default
        return DOMAIN_RATE_LIMITS["default"]

    async def wait(self, domain: str, delay_ms: int | None = None) -> None:
        """Wait if needed to respect rate limits for a domain.

        Uses domain-specific rate limits if no explicit delay provided.
        Respects adaptive delays adjusted by report_result().

        Args:
            domain: Domain name to rate limit
            delay_ms: Optional explicit delay override (milliseconds)
        """
        # Determine delay: explicit override > adaptive delay > domain-specific default
        if delay_ms is not None:
            delay = delay_ms / 1000.0
        elif domain in self._current_delay:
            delay = self._current_delay[domain]
        else:
            # Use domain-specific rate limit as initial default
            domain_limit_ms = self.get_rate_limit(domain)
            delay = domain_limit_ms / 1000.0

        last = self._last_request.get(domain, 0)
        elapsed = time.time() - last
        remaining = delay - elapsed

        if remaining > 0:
            await asyncio.sleep(remaining)

        self._last_request[domain] = time.time()

    def report_result(self, domain: str, status_code: int) -> None:
        """Adjust delay based on server response.

        - 429/503 (rate limit/service unavailable): doubles delay up to max
        - 200 (success): decays delay by 10% toward domain-specific default
        - Other status codes: no adjustment

        Args:
            domain: Domain name that was scraped
            status_code: HTTP status code from response
        """
        # Get domain-specific default for this domain
        domain_default_ms = self.get_rate_limit(domain)
        domain_default = domain_default_ms / 1000.0

        current = self._current_delay.get(domain, domain_default)

        if status_code in (429, 503):
            # Rate limited or service unavailable - back off exponentially
            new_delay = min(current * 2, self._max_delay)
            log.info(
                "rate_limit_backoff",
                domain=domain,
                status_code=status_code,
                old_delay_s=round(current, 2),
                new_delay_s=round(new_delay, 2),
            )
        elif status_code == 200:
            # Success - gradually decay toward domain-specific default
            new_delay = current * 0.9
            if new_delay < domain_default:
                new_delay = domain_default
        else:
            # Other status codes - no adjustment
            return

        self._current_delay[domain] = new_delay

    def reset(self, domain: str) -> None:
        """Reset the rate limit timer and adaptive delay for a domain."""
        self._last_request.pop(domain, None)
        self._current_delay.pop(domain, None)
