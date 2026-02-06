import structlog

from src.db.queries.domains import get_domain_metadata, upsert_domain_metadata
from src.models.scraping import FetchResult, ScrapingTier

log = structlog.get_logger()

_TIER_ORDER = [
    ScrapingTier.BASIC_HTTP,
    ScrapingTier.HEADLESS_BROWSER,
    ScrapingTier.HEADLESS_PROXY,
]


class EscalationService:
    """Manages three-tier adaptive scraping with automatic escalation."""

    def __init__(self, pool: object):
        self.pool = pool

    async def decide_initial_tier(self, domain: str) -> ScrapingTier:
        """Decide the starting tier based on domain history."""
        meta = await get_domain_metadata(self.pool, domain)  # type: ignore[arg-type]

        if meta and meta.last_successful_strategy:
            # Start with the last successful strategy
            try:
                return ScrapingTier(meta.last_successful_strategy)
            except ValueError:
                pass

        return ScrapingTier.BASIC_HTTP

    def should_escalate(self, result: FetchResult) -> bool:
        """Determine if the result warrants tier escalation."""
        return (
            result.blocked
            or result.captcha_detected
            or result.status_code in (403, 429, 503)
            or (result.status_code == 200 and len(result.html) < 200)
        )

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
