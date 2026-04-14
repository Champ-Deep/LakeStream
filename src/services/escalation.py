import json
from typing import Any

import redis.asyncio as redis
import structlog

from src.config.settings import get_settings
from src.db.queries.domains import get_domain_metadata, upsert_domain_metadata
from src.models.scraping import FetchResult, ScrapingTier

log = structlog.get_logger()

# Timeouts (seconds) to wait before escalating to the next tier
ESCALATION_WAIT: dict[tuple[ScrapingTier, ScrapingTier], int] = {
    (ScrapingTier.LIGHTPANDA, ScrapingTier.PLAYWRIGHT): 120,       # 2 min
    (ScrapingTier.PLAYWRIGHT, ScrapingTier.PLAYWRIGHT_PROXY): 600,  # 10 min
}
# Wait before final termination when playwright_proxy also fails
TERMINATION_WAIT_SECONDS: int = 600  # 10 min

# Backward compatibility: map deprecated tier names to new equivalents
_TIER_MIGRATION_MAP = {
    "basic_http": ScrapingTier.LIGHTPANDA,
    "headless_browser": ScrapingTier.PLAYWRIGHT,
    "headless_proxy": ScrapingTier.PLAYWRIGHT_PROXY,
}


def _build_tier_order(proxy_available: bool = True) -> list[ScrapingTier]:
    """Build dynamic tier chain based on configuration.

    - If lightpanda_ws_url is set: [LIGHTPANDA, PLAYWRIGHT, PLAYWRIGHT_PROXY]
    - If no proxy configured: drops PLAYWRIGHT_PROXY
    - If no LightPanda configured: starts at PLAYWRIGHT
    """
    settings = get_settings()
    order: list[ScrapingTier] = []
    if settings.lightpanda_ws_url:
        order.append(ScrapingTier.LIGHTPANDA)
    order.append(ScrapingTier.PLAYWRIGHT)
    if proxy_available:
        order.append(ScrapingTier.PLAYWRIGHT_PROXY)
    return order


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
        client = None

        try:
            client = await redis.from_url(settings.redis_url)
            data = await client.get(key)

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
        finally:
            if client:
                await client.aclose()

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
                tier = ScrapingTier(strategy)
                return tier
            except ValueError:
                pass

        # Default: start at the cheapest available tier
        tier_order = _build_tier_order()
        return tier_order[0]

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

    def get_next_tier(
        self, current: ScrapingTier, proxy_available: bool = True,
    ) -> ScrapingTier | None:
        """Get the next tier in the dynamic escalation chain. Returns None if at max."""
        tier_order = _build_tier_order(proxy_available)
        try:
            idx = tier_order.index(current)
            if idx < len(tier_order) - 1:
                return tier_order[idx + 1]
        except ValueError:
            pass
        return None

    def get_escalation_wait(
        self,
        current: ScrapingTier,
        next_tier: ScrapingTier | None,
        result: FetchResult | None = None,
        proxy_available: bool = True,
    ) -> int:
        """Return seconds to wait before escalating from current to next_tier.

        Waits only apply when the server is rate-limiting (429/503).
        Captcha or tiny-HTML escalations are immediate (no wait).
        Termination wait is skipped when no proxy is available.
        """
        # No point waiting at termination if there's no proxy to escalate to
        if next_tier is None:
            return 0 if not proxy_available else TERMINATION_WAIT_SECONDS

        # Only wait if the block was a rate-limit/server-side rejection, not captcha
        if result is not None and result.captcha_detected and not result.blocked:
            return 0
        if result is not None and result.status_code not in (429, 503):
            # Not a rate-limit — escalate immediately
            return 0

        return ESCALATION_WAIT.get((current, next_tier), 0)

    async def record_result(self, domain: str, result: FetchResult, success: bool) -> None:
        """Record the scraping result for the domain."""
        await upsert_domain_metadata(
            self.pool,  # type: ignore[arg-type]
            domain,
            last_successful_strategy=result.tier_used.value if success else None,
            block_count_increment=1 if result.blocked else 0,
            success=success,
        )
        log.info(
            "escalation_result",
            domain=domain,
            tier=result.tier_used.value,
            success=success,
            blocked=result.blocked,
        )
