"""CRUD queries for discovery_jobs, discovery_job_domains, and tracked_searches."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg

from src.models.discovery import (
    DiscoveryJob,
    DiscoveryJobDomain,
    DiscoveryJobInput,
    DiscoveryStatus,
    TrackedSearch,
    TrackedSearchInput,
)

FREQUENCY_DELTAS = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "biweekly": timedelta(weeks=2),
    "monthly": timedelta(days=30),
}


# --------------- discovery_jobs ---------------


async def create_discovery_job(
    pool: asyncpg.Pool,
    input: DiscoveryJobInput,
    org_id: str,
) -> DiscoveryJob:
    row = await pool.fetchrow(
        """
        INSERT INTO discovery_jobs
            (id, org_id, query, search_mode, search_pages, results_per_page,
             data_types, template_id, max_pages_per_domain, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        RETURNING *
        """,
        uuid4(),
        UUID(org_id),
        input.query,
        input.search_mode,
        input.search_pages,
        input.results_per_page,
        input.data_types,
        input.template_id,
        input.max_pages_per_domain,
        DiscoveryStatus.SEARCHING,
    )
    assert row is not None
    return _parse_discovery_job(row)


async def get_discovery_job(
    pool: asyncpg.Pool,
    discovery_id: UUID,
) -> DiscoveryJob | None:
    row = await pool.fetchrow(
        "SELECT * FROM discovery_jobs WHERE id = $1",
        discovery_id,
    )
    if row is None:
        return None
    return _parse_discovery_job(row)


async def update_discovery_status(
    pool: asyncpg.Pool,
    discovery_id: UUID,
    status: DiscoveryStatus,
    *,
    domains_found: int | None = None,
    domains_skipped: int | None = None,
    search_results: list | dict | None = None,
    error_message: str | None = None,
    total_cost_usd: float | None = None,
    completed_at: datetime | None = None,
) -> None:
    sets = ["status = $2"]
    vals: list[object] = [discovery_id, status]
    idx = 3

    for field, value in [
        ("domains_found", domains_found),
        ("domains_skipped", domains_skipped),
        ("error_message", error_message),
        ("total_cost_usd", total_cost_usd),
        ("completed_at", completed_at),
    ]:
        if value is not None:
            sets.append(f"{field} = ${idx}")
            vals.append(value)
            idx += 1

    # JSONB field needs special handling
    if search_results is not None:
        sets.append(f"search_results = ${idx}::jsonb")
        vals.append(json.dumps(search_results))
        idx += 1

    query = f"UPDATE discovery_jobs SET {', '.join(sets)} WHERE id = $1"
    await pool.execute(query, *vals)


def _parse_discovery_job(row: asyncpg.Record) -> DiscoveryJob:
    data = dict(row)
    if isinstance(data.get("search_results"), str):
        data["search_results"] = json.loads(data["search_results"])
    return DiscoveryJob(**data)


# --------------- discovery_job_domains ---------------


async def insert_discovery_domain(
    pool: asyncpg.Pool,
    *,
    discovery_id: UUID,
    domain: str,
    source_url: str,
    source_title: str | None = None,
    source_snippet: str | None = None,
    source_score: float | None = None,
    scrape_job_id: UUID | None = None,
    status: str = "pending",
    skip_reason: str | None = None,
) -> UUID:
    domain_id = uuid4()
    await pool.execute(
        """
        INSERT INTO discovery_job_domains
            (id, discovery_id, domain, scrape_job_id,
             source_url, source_title, source_snippet, source_score,
             status, skip_reason)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        domain_id,
        discovery_id,
        domain,
        scrape_job_id,
        source_url,
        source_title,
        source_snippet,
        source_score,
        status,
        skip_reason,
    )
    return domain_id


async def update_discovery_domain_status(
    pool: asyncpg.Pool,
    domain_id: UUID,
    status: str,
    *,
    scrape_job_id: UUID | None = None,
    skip_reason: str | None = None,
) -> None:
    sets = ["status = $2"]
    vals: list[object] = [domain_id, status]
    idx = 3

    if scrape_job_id is not None:
        sets.append(f"scrape_job_id = ${idx}")
        vals.append(scrape_job_id)
        idx += 1
    if skip_reason is not None:
        sets.append(f"skip_reason = ${idx}")
        vals.append(skip_reason)
        idx += 1

    query = f"UPDATE discovery_job_domains SET {', '.join(sets)} WHERE id = $1"
    await pool.execute(query, *vals)


async def get_discovery_domains(
    pool: asyncpg.Pool,
    discovery_id: UUID,
) -> list[DiscoveryJobDomain]:
    rows = await pool.fetch(
        "SELECT * FROM discovery_job_domains WHERE discovery_id = $1 ORDER BY created_at",
        discovery_id,
    )
    return [DiscoveryJobDomain(**dict(row)) for row in rows]


# --------------- domain dedup helpers ---------------


async def get_recently_scraped_domains(
    pool: asyncpg.Pool,
    days: int = 7,
) -> set[str]:
    """Return domains scraped within the last N days."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = await pool.fetch(
        "SELECT domain FROM domain_metadata WHERE last_scraped_at >= $1",
        cutoff,
    )
    return {row["domain"] for row in rows}


# --------------- tracked_searches ---------------


async def create_tracked_search(
    pool: asyncpg.Pool,
    input: TrackedSearchInput,
    org_id: str,
) -> TrackedSearch:
    delta = FREQUENCY_DELTAS.get(input.scrape_frequency, timedelta(weeks=1))
    next_run = datetime.now(UTC) + delta

    row = await pool.fetchrow(
        """
        INSERT INTO tracked_searches
            (id, org_id, query, search_mode, search_pages, results_per_page,
             data_types, template_id, max_pages_per_domain,
             scrape_frequency, webhook_url, next_run_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        RETURNING *
        """,
        uuid4(),
        UUID(org_id),
        input.query,
        input.search_mode,
        input.search_pages,
        input.results_per_page,
        input.data_types,
        input.template_id,
        input.max_pages_per_domain,
        input.scrape_frequency,
        input.webhook_url,
        next_run,
    )
    assert row is not None
    return TrackedSearch(**dict(row))


async def list_tracked_searches(
    pool: asyncpg.Pool,
    *,
    active_only: bool = True,
) -> list[TrackedSearch]:
    condition = "WHERE is_active = true" if active_only else ""
    rows = await pool.fetch(f"SELECT * FROM tracked_searches {condition} ORDER BY created_at DESC")
    return [TrackedSearch(**dict(row)) for row in rows]


async def get_tracked_search(
    pool: asyncpg.Pool,
    tracked_id: UUID,
) -> TrackedSearch | None:
    row = await pool.fetchrow(
        "SELECT * FROM tracked_searches WHERE id = $1",
        tracked_id,
    )
    return TrackedSearch(**dict(row)) if row else None


async def delete_tracked_search(
    pool: asyncpg.Pool,
    tracked_id: UUID,
) -> None:
    """Soft-delete a tracked search."""
    await pool.execute(
        "UPDATE tracked_searches SET is_active = false WHERE id = $1",
        tracked_id,
    )


async def get_due_tracked_searches(
    pool: asyncpg.Pool,
) -> list[TrackedSearch]:
    """Get tracked searches that are due for their next run."""
    rows = await pool.fetch(
        "SELECT * FROM tracked_searches WHERE is_active = true AND next_run_at <= NOW()"
    )
    return [TrackedSearch(**dict(row)) for row in rows]


async def mark_tracked_search_run(
    pool: asyncpg.Pool,
    tracked_id: UUID,
    domains_discovered: int = 0,
) -> None:
    """Update timestamps and counters after a tracked search run."""
    row = await pool.fetchrow(
        "SELECT scrape_frequency FROM tracked_searches WHERE id = $1",
        tracked_id,
    )
    if not row:
        return
    delta = FREQUENCY_DELTAS.get(row["scrape_frequency"], timedelta(weeks=1))
    await pool.execute(
        """
        UPDATE tracked_searches SET
            last_run_at = NOW(),
            next_run_at = NOW() + $2,
            total_runs = total_runs + 1,
            total_domains_discovered = total_domains_discovered + $3
        WHERE id = $1
        """,
        tracked_id,
        delta,
        domains_discovered,
    )
