"""Queries for page_links — the site link graph edges (v2). See migration 027."""

from uuid import UUID

import asyncpg


async def bulk_insert_page_links(
    pool: asyncpg.Pool,
    *,
    job_id: UUID | None,
    user_id: UUID | None,
    domain: str,
    edges: list[tuple[str, str]],
) -> int:
    """Insert (source, target) edges, ignoring duplicates. Returns count attempted."""
    if not edges:
        return 0
    rows = [(job_id, user_id, domain, src, tgt) for src, tgt in edges]
    await pool.executemany(
        """
        INSERT INTO page_links (job_id, user_id, domain, source_url, target_url)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (job_id, source_url, target_url) DO NOTHING
        """,
        rows,
    )
    return len(rows)


async def get_edges_by_domain(
    pool: asyncpg.Pool, domain: str, user_id: UUID | None = None, limit: int = 2000
) -> list[asyncpg.Record]:
    """Return edges for a domain. Admins (user_id=None) see all users' edges."""
    if user_id is None:
        return await pool.fetch(
            "SELECT DISTINCT source_url, target_url FROM page_links "
            "WHERE domain = $1 LIMIT $2",
            domain,
            limit,
        )
    return await pool.fetch(
        "SELECT DISTINCT source_url, target_url FROM page_links "
        "WHERE domain = $1 AND user_id IS NOT DISTINCT FROM $2 LIMIT $3",
        domain,
        user_id,
        limit,
    )
