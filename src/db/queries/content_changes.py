"""Queries for content_changes — the page-level change log (v2). See migration 025."""

from uuid import UUID

import asyncpg


async def insert_change(
    pool: asyncpg.Pool,
    *,
    domain: str,
    url: str,
    old_hash: str | None,
    new_hash: str,
    user_id: UUID | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO content_changes (domain, url, old_hash, new_hash, user_id)
        VALUES ($1, $2, $3, $4, $5)
        """,
        domain,
        url,
        old_hash,
        new_hash,
        user_id,
    )


async def get_recent_changes_by_domain(
    pool: asyncpg.Pool, domain: str, user_id: UUID | None = None, limit: int = 50
) -> list[asyncpg.Record]:
    """Recent changes for a domain. Admins (user_id=None) see all users' changes."""
    if user_id is None:
        return await pool.fetch(
            "SELECT * FROM content_changes WHERE domain = $1 "
            "ORDER BY changed_at DESC LIMIT $2",
            domain,
            limit,
        )
    return await pool.fetch(
        "SELECT * FROM content_changes WHERE domain = $1 AND user_id = $2 "
        "ORDER BY changed_at DESC LIMIT $3",
        domain,
        user_id,
        limit,
    )
