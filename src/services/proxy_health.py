"""Proxy health tracking with weighted selection and region filtering.

Replaces the simple round-robin proxy pool rotation with health-aware
selection. Tracks success rate, latency, and failure counts per proxy
URL in Redis. Proxies with repeated failures are temporarily backed off.

Does NOT replace the failover chain in LakePlaywrightProxyFetcher —
only replaces the _next_pool_proxy() round-robin for pool selection.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass

import redis.asyncio as redis
import structlog

from src.config.settings import get_settings

log = structlog.get_logger()

HEALTH_KEY_PREFIX = "proxy:health"
BACKOFF_SECONDS = 300  # 5 minutes
MAX_CONSECUTIVE_FAILURES = 5

# Region → Accept-Language + timezone mapping
REGION_HEADERS: dict[str, dict[str, str]] = {
    "us": {"Accept-Language": "en-US,en;q=0.9", "timezone": "America/New_York"},
    "eu": {"Accept-Language": "en-GB,en;q=0.9,de;q=0.8", "timezone": "Europe/London"},
    "uk": {"Accept-Language": "en-GB,en;q=0.9", "timezone": "Europe/London"},
    "de": {"Accept-Language": "de-DE,de;q=0.9,en;q=0.8", "timezone": "Europe/Berlin"},
    "asia": {"Accept-Language": "en;q=0.9,ja;q=0.8", "timezone": "Asia/Tokyo"},
    "in": {"Accept-Language": "en-IN,en;q=0.9,hi;q=0.8", "timezone": "Asia/Kolkata"},
    "au": {"Accept-Language": "en-AU,en;q=0.9", "timezone": "Australia/Sydney"},
}


@dataclass
class ProxyHealth:
    url: str
    region: str | None = None
    success_count: int = 0
    fail_count: int = 0
    avg_latency_ms: int = 0
    last_failure_at: float = 0.0
    consecutive_failures: int = 0

    @property
    def total_requests(self) -> int:
        return self.success_count + self.fail_count

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 1.0  # Assume healthy until proven otherwise
        return self.success_count / self.total_requests

    @property
    def is_backed_off(self) -> bool:
        if self.consecutive_failures < MAX_CONSECUTIVE_FAILURES:
            return False
        return (time.time() - self.last_failure_at) < BACKOFF_SECONDS


def _url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


class ProxyHealthTracker:
    """Redis-backed proxy health tracker with weighted random selection."""

    def __init__(self, redis_client: redis.Redis | None = None) -> None:
        self._redis = redis_client

    async def _get_redis(self) -> redis.Redis:
        if self._redis is None:
            settings = get_settings()
            self._redis = redis.from_url(settings.redis_url)
        return self._redis

    def _key(self, url: str) -> str:
        return f"{HEALTH_KEY_PREFIX}:{_url_hash(url)}"

    async def _load(self, url: str) -> ProxyHealth:
        client = await self._get_redis()
        data = await client.get(self._key(url))
        if data:
            try:
                d = json.loads(data)
                return ProxyHealth(**d)
            except (json.JSONDecodeError, TypeError):
                pass
        return ProxyHealth(url=url)

    async def _save(self, health: ProxyHealth) -> None:
        client = await self._get_redis()
        data = {
            "url": health.url,
            "region": health.region,
            "success_count": health.success_count,
            "fail_count": health.fail_count,
            "avg_latency_ms": health.avg_latency_ms,
            "last_failure_at": health.last_failure_at,
            "consecutive_failures": health.consecutive_failures,
        }
        # TTL of 24 hours — stale health data auto-expires
        await client.set(self._key(health.url), json.dumps(data), ex=86400)

    async def record_success(self, url: str, latency_ms: int) -> None:
        """Record a successful request through this proxy."""
        health = await self._load(url)
        health.success_count += 1
        health.consecutive_failures = 0

        # Running average of latency
        if health.avg_latency_ms == 0:
            health.avg_latency_ms = latency_ms
        else:
            # Exponential moving average (alpha=0.3 for responsiveness)
            health.avg_latency_ms = int(0.7 * health.avg_latency_ms + 0.3 * latency_ms)

        await self._save(health)

    async def record_failure(self, url: str) -> None:
        """Record a failed request through this proxy."""
        health = await self._load(url)
        health.fail_count += 1
        health.consecutive_failures += 1
        health.last_failure_at = time.time()
        await self._save(health)

    async def pick_proxy(
        self,
        proxies: list[dict[str, str]],
        region: str | None = None,
    ) -> dict[str, str] | None:
        """Pick the best proxy using weighted random selection.

        Weight = success_rate * (1000 / max(avg_latency_ms, 1))
        Proxies with >5 consecutive failures are skipped (5-min backoff).
        If region is specified, only proxies tagged with that region are considered.

        Args:
            proxies: List of proxy config dicts. Each must have "url" key.
                     Optional "region" key for geo-filtering.
            region: Optional region filter (e.g., "us", "eu", "asia").

        Returns:
            Selected proxy config dict, or None if all are backed off.
        """
        if not proxies:
            return None

        # Filter by region if requested
        candidates = proxies
        if region:
            region_lower = region.lower()
            filtered = [p for p in proxies if p.get("region", "").lower() == region_lower]
            if filtered:
                candidates = filtered
            # If no region match, fall through to all proxies

        # Score each candidate
        scored: list[tuple[dict[str, str], float]] = []
        for proxy in candidates:
            url = proxy.get("url") or proxy.get("server", "")
            if not url:
                continue

            health = await self._load(url)

            # Skip backed-off proxies
            if health.is_backed_off:
                log.debug(
                    "proxy_backed_off",
                    url=url,
                    consecutive_failures=health.consecutive_failures,
                )
                continue

            # Weight: success_rate * inverse_latency
            latency_factor = 1000 / max(health.avg_latency_ms, 1)
            weight = health.success_rate * latency_factor

            # Minimum weight so new proxies still get selected
            weight = max(weight, 0.01)

            scored.append((proxy, weight))

        if not scored:
            # All backed off — return first proxy as fallback
            log.warning("all_proxies_backed_off", count=len(candidates))
            return candidates[0] if candidates else None

        # Weighted random selection
        total_weight = sum(w for _, w in scored)
        r = random.uniform(0, total_weight)
        cumulative = 0.0
        for proxy, weight in scored:
            cumulative += weight
            if r <= cumulative:
                return proxy

        # Shouldn't reach here, but fallback
        return scored[0][0]

    async def set_region(self, url: str, region: str) -> None:
        """Tag a proxy with a region."""
        health = await self._load(url)
        health.region = region
        await self._save(health)

    async def get_all_health(self) -> list[ProxyHealth]:
        """Get health stats for all tracked proxies."""
        client = await self._get_redis()
        keys = []
        async for key in client.scan_iter(f"{HEALTH_KEY_PREFIX}:*"):
            keys.append(key)

        results = []
        for key in keys:
            data = await client.get(key)
            if data:
                try:
                    d = json.loads(data)
                    results.append(ProxyHealth(**d))
                except (json.JSONDecodeError, TypeError):
                    continue

        return results


def get_region_headers(region: str | None) -> dict[str, str]:
    """Get Accept-Language headers for a region."""
    if not region:
        return {}
    return {
        k: v
        for k, v in REGION_HEADERS.get(region.lower(), {}).items()
        if k != "timezone"
    }


def get_region_timezone(region: str | None) -> str:
    """Get timezone for a region (for Playwright context)."""
    if not region:
        return "America/New_York"
    info = REGION_HEADERS.get(region.lower(), {})
    return info.get("timezone", "America/New_York")
