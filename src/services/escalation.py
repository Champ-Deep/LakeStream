import json
from typing import Any

import redis.asyncio as redis
import structlog

from src.config.settings import get_settings
from src.db.queries.domains import get_domain_metadata, upsert_domain_metadata
from src.models.scraping import FetchResult, ScrapingTier

log = structlog.get_logger()

_TIER_ORDER = [
    ScrapingTier.PLAYWRIGHT,
    ScrapingTier.PLAYWRIGHT_PROXY,
]

# Backward compatibility: map deprecated tier names to new equivalents
_TIER_MIGRATION_MAP = {
    "basic_http": ScrapingTier.PLAYWRIGHT,
    "headless_browser": ScrapingTier.PLAYWRIGHT,
    "headless_proxy": ScrapingTier.PLAYWRIGHT_PROXY,
}


class EscalationService:
    """Manages three-tier adaptive scraping with automatic escalation.

    Includes session health tracking for domains like LinkedIn to optimize
    tier selection based on session state and usage.
    """

    def __init__(self, pool: object):
        self.pool = pool

    async def _check_session_health(self, domain: str) -> dict[str, Any] | None:
        """Check health of existing Playwright session for a domain.

        Connects to Redis and retrieves session metadata to assess:
        - Whether session exists
        - Authentication status
        - Request count (session age)
        - Last used timestamp

        Args:
            domain: Domain to check session for (e.g., "linkedin.com")

        Returns:
            Session metadata dict if session exists and is valid, None otherwise.
            Session dict contains: storage_state, created_at, last_used_at,
            request_count, authenticated
        """
        settings = get_settings()
        key = f"playwright_session:{domain}"

        try:
            client = await redis.from_url(settings.redis_url)
            data = await client.get(key)
            await client.aclose()

            if data:
                session = json.loads(data)
                log.debug(
                    "session_health_check",
                    domain=domain,
                    authenticated=session.get("authenticated", False),
                    request_count=session.get("request_count", 0),
                )
                return session
        except Exception as exc:
            log.warning(
                "session_health_check_error",
                domain=domain,
                error=str(exc),
                error_type=type(exc).__name__,
            )

        return None

    async def decide_initial_tier(self, domain: str) -> ScrapingTier:
        """Decide the starting tier based on domain history and session health.

        Handles automatic migration from deprecated tiers:
        - headless_browser → playwright
        - headless_proxy → playwright_proxy

        For LinkedIn domains, uses session health tracking to optimize tier:
        - Healthy session (authenticated, <50 requests): PLAYWRIGHT (no proxy)
        - Aging session (>=50 requests): PLAYWRIGHT_PROXY (preemptive proxy)
        - No session or unhealthy: follow normal escalation
        """
        # Special handling for LinkedIn domains - optimize based on session health
        linkedin_domains = ["linkedin.com", "www.linkedin.com", "sales.linkedin.com"]
        if domain in linkedin_domains or domain.endswith(".linkedin.com"):
            session = await self._check_session_health(domain)

            if session:
                authenticated = session.get("authenticated", False)
                request_count = session.get("request_count", 0)

                # Session is healthy and fresh - use PLAYWRIGHT without proxy
                if authenticated and request_count < 50:
                    log.info(
                        "session_health_tier_decision",
                        domain=domain,
                        decision="playwright",
                        reason="healthy_session",
                        request_count=request_count,
                    )
                    return ScrapingTier.PLAYWRIGHT

                # Session is aging - preemptively use proxy to avoid ban
                if authenticated and request_count >= 50:
                    log.info(
                        "session_health_tier_decision",
                        domain=domain,
                        decision="playwright_proxy",
                        reason="aging_session",
                        request_count=request_count,
                    )
                    return ScrapingTier.PLAYWRIGHT_PROXY

        # Normal domain history-based tier selection
        meta = await get_domain_metadata(self.pool, domain)  # type: ignore[arg-type]

        if meta and meta.last_successful_strategy:
            strategy = meta.last_successful_strategy

            # Check if this is a deprecated tier that needs migration
            if strategy in _TIER_MIGRATION_MAP:
                migrated_tier = _TIER_MIGRATION_MAP[strategy]
                log.info(
                    "tier_migration",
                    domain=domain,
                    old_tier=strategy,
                    new_tier=migrated_tier.value,
                )
                return migrated_tier

            # Use the strategy directly if it's a current tier
            try:
                return ScrapingTier(strategy)
            except ValueError:
                pass

        return ScrapingTier.PLAYWRIGHT

    def should_escalate(self, result: FetchResult) -> bool:
        """Determine if result warrants tier escalation.

        Blocked detection happens at fetch layer. This just checks the flags.
        """
        return result.blocked or result.captcha_detected

    def get_escalation_reason(self, result: FetchResult) -> str:
        """Return human-readable reason for escalation (for logging)."""
        reasons = []

        if result.blocked:
            reasons.append("blocked_flag")
        if result.captcha_detected:
            reasons.append("captcha")

        return ", ".join(reasons) if reasons else "none"

    def get_next_tier(self, current: ScrapingTier) -> ScrapingTier | None:
        """Get the next tier in the escalation chain. Returns None if at max."""
        try:
            idx = _TIER_ORDER.index(current)
            if idx < len(_TIER_ORDER) - 1:
                return _TIER_ORDER[idx + 1]
        except ValueError:
            pass
        return None

    async def record_result(self, domain: str, result: FetchResult, success: bool) -> None:
        """Record the scraping result for the domain."""
        await upsert_domain_metadata(
            self.pool,  # type: ignore[arg-type]
            domain,
            last_successful_strategy=result.tier_used.value if success else None,
            block_count_increment=1 if result.blocked else 0,
        )
        log.info(
            "escalation_result",
            domain=domain,
            tier=result.tier_used.value,
            success=success,
            blocked=result.blocked,
        )
