"""CRUD queries for the tracked_domains table."""

from datetime import UTC, datetime, timedelta

import asyncpg

from src.models.tracked_domain import TrackedDomain

FREQUENCY_DELTAS = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "biweekly": timedelta(weeks=2),
    "monthly": timedelta(days=30),
}


async def add_tracked_domain(
    pool: asyncpg.Pool,
    domain: str,
    *,
    data_types: list[str] | None = None,
    scrape_frequency: str = "weekly",
    max_pages: int = 100,
    template_id: str = "auto",
    webhook_url: str | None = None,
) -> TrackedDomain:
    """Insert or update a tracked domain."""
    if data_types is None:
        data_types = [
            "blog_url",
            "article",
            "contact",
            "tech_stack",
            "resource",
            "pricing",
        ]
    delta = FREQUENCY_DELTAS.get(scrape_frequency, timedelta(weeks=1))
    next_scrape = datetime.now(UTC) + delta

    row = await pool.fetchrow(
        """
        INSERT INTO tracked_domains
            (domain, data_types, scrape_frequency, max_pages,
             template_id, webhook_url, next_scrape_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (domain) DO UPDATE SET
            data_types = $2,
            scrape_frequency = $3,
            max_pages = $4,
            template_id = $5,
            webhook_url = $6,
            is_active = true,
            next_scrape_at = $7,
            updated_at = NOW()
        RETURNING *
        """,
        domain,
        data_types,
        scrape_frequency,
        max_pages,
        template_id,
        webhook_url,
        next_scrape,
    )
    return TrackedDomain(**dict(row))


async def list_tracked_domains(
    pool: asyncpg.Pool, *, active_only: bool = True
) -> list[TrackedDomain]:
    """List tracked domains."""
    condition = "WHERE is_active = true" if active_only else ""
    rows = await pool.fetch(f"SELECT * FROM tracked_domains {condition} ORDER BY domain ASC")
    return [TrackedDomain(**dict(row)) for row in rows]


async def get_tracked_domain(pool: asyncpg.Pool, domain: str) -> TrackedDomain | None:
    """Get a single tracked domain."""
    row = await pool.fetchrow("SELECT * FROM tracked_domains WHERE domain = $1", domain)
    return TrackedDomain(**dict(row)) if row else None


async def remove_tracked_domain(pool: asyncpg.Pool, domain: str) -> None:
    """Soft-delete a tracked domain."""
    await pool.execute(
        "UPDATE tracked_domains SET is_active = false, updated_at = NOW() WHERE domain = $1",
        domain,
    )


async def get_due_domains(pool: asyncpg.Pool) -> list[TrackedDomain]:
    """Get tracked domains that are due for their next auto-scrape."""
    rows = await pool.fetch(
        "SELECT * FROM tracked_domains WHERE is_active = true AND next_scrape_at <= NOW()"
    )
    return [TrackedDomain(**dict(row)) for row in rows]


async def mark_scraped(pool: asyncpg.Pool, domain: str) -> None:
    """Update last/next scrape timestamps after an auto-scrape."""
    row = await pool.fetchrow(
        "SELECT scrape_frequency FROM tracked_domains WHERE domain = $1",
        domain,
    )
    if not row:
        return
    freq = row["scrape_frequency"]
    delta = FREQUENCY_DELTAS.get(freq, timedelta(weeks=1))
    await pool.execute(
        "UPDATE tracked_domains "
        "SET last_auto_scraped_at = NOW(), "
        "    next_scrape_at = NOW() + $2, "
        "    updated_at = NOW() "
        "WHERE domain = $1",
        domain,
        delta,
    )
