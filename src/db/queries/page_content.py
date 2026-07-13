"""Queries for page_content — the durable per-URL content cache (v2).

See migration 024. Keyed UNIQUE(user_id, url) so upsert gives cache + diff.
"""

from uuid import UUID

import asyncpg


async def upsert_page_content(
    pool: asyncpg.Pool,
    *,
    domain: str,
    url: str,
    content_hash: str,
    markdown: str | None = None,
    raw_html: str | None = None,
    title: str | None = None,
    screenshot_path: str | None = None,
    org_id: UUID | None = None,
    user_id: UUID | None = None,
) -> None:
    """Insert or update the cached content for (user_id, url)."""
    await pool.execute(
        """
        INSERT INTO page_content (
            domain, url, content_hash, markdown, raw_html, title,
            screenshot_path, org_id, user_id, fetched_at, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), NOW())
        ON CONFLICT (user_id, url) DO UPDATE SET
            domain = EXCLUDED.domain,
            content_hash = EXCLUDED.content_hash,
            markdown = EXCLUDED.markdown,
            raw_html = EXCLUDED.raw_html,
            title = COALESCE(EXCLUDED.title, page_content.title),
            screenshot_path = COALESCE(EXCLUDED.screenshot_path, page_content.screenshot_path),
            fetched_at = NOW(),
            updated_at = NOW()
        """,
        domain,
        url,
        content_hash,
        markdown,
        raw_html,
        title,
        screenshot_path,
        org_id,
        user_id,
    )


async def get_page_content_by_url(
    pool: asyncpg.Pool, url: str, user_id: UUID | None = None
) -> asyncpg.Record | None:
    """Return the cached row for a URL scoped to a user (NULL user_id = shared)."""
    return await pool.fetchrow(
        "SELECT * FROM page_content WHERE url = $1 AND user_id IS NOT DISTINCT FROM $2",
        url,
        user_id,
    )


async def get_hash(pool: asyncpg.Pool, url: str, user_id: UUID | None = None) -> str | None:
    """Return the stored content_hash for a URL, or None if not cached."""
    return await pool.fetchval(
        "SELECT content_hash FROM page_content WHERE url = $1 AND user_id IS NOT DISTINCT FROM $2",
        url,
        user_id,
    )


async def touch_fetched_at(pool: asyncpg.Pool, url: str, user_id: UUID | None = None) -> None:
    """Bump fetched_at on a cache hit without rewriting content."""
    await pool.execute(
        "UPDATE page_content SET fetched_at = NOW() "
        "WHERE url = $1 AND user_id IS NOT DISTINCT FROM $2",
        url,
        user_id,
    )
