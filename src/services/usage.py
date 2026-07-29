"""API usage metering (v2.1).

record_usage() is fire-and-forget from routes/middleware — a metering failure
must never fail the request. Credits: one successful billable call = 1 credit.
"""

from datetime import UTC, datetime
from uuid import UUID

import structlog
from asyncpg import Pool

from src.config.settings import get_settings

log = structlog.get_logger()

# Endpoint prefixes that consume a credit on success (POST only)
BILLABLE_PREFIXES = (
    "/api/scrape",
    "/api/parse",
    "/api/search",
    "/api/enrich",
    "/api/discover",
)


async def record_usage(
    pool: Pool,
    *,
    endpoint: str,
    user_id: UUID | None,
    org_id: UUID | None = None,
    api_key_id: UUID | None = None,
    credits: int = 1,
) -> None:
    try:
        await pool.execute(
            "INSERT INTO api_usage (api_key_id, user_id, org_id, endpoint, credits) "
            "VALUES ($1, $2, $3, $4, $5)",
            api_key_id,
            user_id,
            org_id,
            endpoint,
            credits,
        )
    except Exception as e:
        log.warning("usage_record_failed", endpoint=endpoint, error=str(e))


async def credits_used_this_month(pool: Pool, user_id: UUID) -> int:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (
        await pool.fetchval(
            "SELECT COALESCE(SUM(credits), 0) FROM api_usage "
            "WHERE user_id = $1 AND created_at >= $2",
            user_id,
            start,
        )
        or 0
    )


async def over_credit_limit(pool: Pool, user_id: UUID | None) -> bool:
    """True when enforcement is on and the user is past the monthly allowance."""
    settings = get_settings()
    if not settings.enable_credit_enforcement or user_id is None:
        return False
    used = await credits_used_this_month(pool, user_id)
    return used >= settings.free_monthly_credits


async def usage_summary(pool: Pool, user_id: UUID) -> dict:
    settings = get_settings()
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = await pool.fetch(
        "SELECT endpoint, SUM(credits) AS credits, COUNT(*) AS calls "
        "FROM api_usage WHERE user_id = $1 AND created_at >= $2 "
        "GROUP BY endpoint ORDER BY credits DESC",
        user_id,
        start,
    )
    total = sum(r["credits"] for r in rows)
    return {
        "period_start": start.isoformat(),
        "credits_used": total,
        "monthly_limit": settings.free_monthly_credits if settings.enable_credit_enforcement else None,
        "enforcement": settings.enable_credit_enforcement,
        "by_endpoint": [
            {"endpoint": r["endpoint"], "credits": r["credits"], "calls": r["calls"]}
            for r in rows
        ],
    }
