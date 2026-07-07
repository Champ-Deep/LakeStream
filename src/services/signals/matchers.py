"""Signal-type-specific matchers.

Each `check_*_signal` function queries `scraped_data` with a heuristic specific
to its signal type and, if it finds matches, shapes them into a common result
dict. The queries themselves are genuinely different (different filters, and
`check_hiring_spike_signal` aggregates by domain instead of returning raw
rows), so they are kept as separate functions rather than forced behind one
generic query-builder. What *is* identical across all four is the shape of a
successful result, so that part is factored into `_build_match_result`.
"""

from typing import Any
from uuid import UUID

from asyncpg import Pool

from src.models.signals import Signal


def _build_match_result(
    rows: list[Any], signal_type: str, trigger: str
) -> dict[str, Any] | None:
    """Shape matched rows into the common signal-match result dict.

    Returns None if there are no matches, so callers can `return
    _build_match_result(...)` directly.
    """
    if not rows:
        return None

    matches = [dict(row) for row in rows]
    return {
        "matches": matches,
        "match_count": len(matches),
        "signal_type": signal_type,
        "trigger": trigger,
    }


async def check_job_change_signal(
    pool: Pool, signal: Signal, org_id: UUID
) -> dict[str, Any] | None:
    """Check if job change conditions match recent data."""
    filters = signal.trigger_config.get("filters", {})
    job_title = filters.get("job_title_contains", "")

    # Query scraped data for recent job changes (last 24 hours)
    query = """
        SELECT * FROM scraped_data
        WHERE org_id = $1
        AND data_type = 'contact'
        AND metadata->>'job_title' ILIKE $2
        AND scraped_at > NOW() - INTERVAL '24 hours'
        ORDER BY scraped_at DESC
        LIMIT 100
    """

    rows = await pool.fetch(query, org_id, f"%{job_title}%")

    return _build_match_result(
        rows,
        "job_change",
        f"Found {len(rows)} contacts with job title containing '{job_title}'",
    )


async def check_funding_signal(pool: Pool, signal: Signal, org_id: UUID) -> dict[str, Any] | None:
    """Check if funding round conditions match recent data."""
    _filters = signal.trigger_config.get("filters", {})  # noqa: F841

    # In a real implementation, this would query funding data sources
    # For now, check scraped_data for funding mentions
    query = """
        SELECT * FROM scraped_data
        WHERE org_id = $1
        AND (
            metadata->>'type' = 'funding'
            OR metadata->>'category' = 'funding'
        )
        AND scraped_at > NOW() - INTERVAL '7 days'
        LIMIT 50
    """

    rows = await pool.fetch(query, org_id)

    return _build_match_result(
        rows, "funding_round", f"Found {len(rows)} funding announcements"
    )


async def check_tech_stack_signal(
    pool: Pool, signal: Signal, org_id: UUID
) -> dict[str, Any] | None:
    """Check if tech stack change conditions match recent data."""
    filters = signal.trigger_config.get("filters", {})
    technology = filters.get("technology", "")

    # Query scraped data for tech stack changes
    query = """
        SELECT * FROM scraped_data
        WHERE org_id = $1
        AND data_type = 'tech_stack'
        AND (
            metadata->>'platform' ILIKE $2
            OR metadata->>'technology' ILIKE $2
        )
        AND scraped_at > NOW() - INTERVAL '7 days'
        LIMIT 50
    """

    rows = await pool.fetch(query, org_id, f"%{technology}%")

    return _build_match_result(
        rows, "tech_stack_change", f"Found {len(rows)} companies using {technology}"
    )


async def check_hiring_spike_signal(
    pool: Pool, signal: Signal, org_id: UUID
) -> dict[str, Any] | None:
    """Check if hiring volume spike conditions match."""
    filters = signal.trigger_config.get("filters", {})
    _department = filters.get("department", "All")  # noqa: F841
    spike_threshold = filters.get("spike_threshold", 3)  # 3x normal

    # Query for recent job postings
    query = """
        SELECT domain, COUNT(*) as job_count
        FROM scraped_data
        WHERE org_id = $1
        AND data_type = 'job_posting'
        AND scraped_at > NOW() - INTERVAL '7 days'
        GROUP BY domain
        HAVING COUNT(*) >= $2
    """

    rows = await pool.fetch(query, org_id, spike_threshold * 2)  # Simplified threshold

    return _build_match_result(
        rows, "hiring_spike", f"Found {len(rows)} companies with hiring spikes"
    )
