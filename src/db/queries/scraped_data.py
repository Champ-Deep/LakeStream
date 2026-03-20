import json
from uuid import UUID, uuid4

import asyncpg

from src.models.scraped_data import ScrapedData

_UPSERT_SQL = """
    INSERT INTO scraped_data
        (id, job_id, domain, data_type, url, title, metadata, org_id, user_id)
    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9)
    ON CONFLICT (domain, url, data_type) WHERE url IS NOT NULL
    DO UPDATE SET
        job_id = EXCLUDED.job_id,
        title = EXCLUDED.title,
        metadata = EXCLUDED.metadata,
        org_id = EXCLUDED.org_id,
        user_id = EXCLUDED.user_id,
        scraped_at = NOW()
"""


async def insert_scraped_data(
    pool: asyncpg.Pool,
    *,
    job_id: UUID,
    domain: str,
    data_type: str,
    url: str | None = None,
    title: str | None = None,
    metadata: dict | None = None,
    org_id: UUID | None = None,
) -> UUID:
    record_id = uuid4()
    await pool.execute(
        _UPSERT_SQL,
        record_id,
        job_id,
        domain,
        data_type,
        url,
        title,
        json.dumps(metadata or {}),
        org_id,
        None,  # user_id
    )
    return record_id


async def batch_insert_scraped_data(
    pool: asyncpg.Pool,
    records: list[dict],
) -> int:
    """Upsert multiple scraped_data records in a single transaction.

    Uses ON CONFLICT to update existing records (same domain+url+data_type)
    instead of creating duplicates.
    """
    if not records:
        return 0

    values = []
    for rec in records:
        values.append(
            (
                uuid4(),
                rec["job_id"],
                rec["domain"],
                rec["data_type"],
                rec.get("url"),
                rec.get("title"),
                json.dumps(rec.get("metadata", {})),
                rec.get("org_id"),
                rec.get("user_id"),
            )
        )

    await pool.executemany(_UPSERT_SQL, values)
    return len(values)


def _parse_row(row: asyncpg.Record) -> ScrapedData:
    """Parse a database row into a ScrapedData model, handling JSONB conversion."""
    data = dict(row)
    # asyncpg may return JSONB as string - ensure it's a dict
    if isinstance(data.get("metadata"), str):
        data["metadata"] = json.loads(data["metadata"])
    return ScrapedData(**data)


async def get_scraped_data_by_job(pool: asyncpg.Pool, job_id: UUID) -> list[ScrapedData]:
    rows = await pool.fetch(
        "SELECT * FROM scraped_data WHERE job_id = $1 ORDER BY scraped_at", job_id
    )
    return [_parse_row(row) for row in rows]


async def count_scraped_data_by_job(pool: asyncpg.Pool, job_id: UUID) -> int:
    count = await pool.fetchval("SELECT COUNT(*) FROM scraped_data WHERE job_id = $1", job_id)
    return count or 0


async def get_data_type_counts(pool: asyncpg.Pool, job_id: UUID) -> dict[str, int]:
    """Return {data_type: count} for a job, excluding raw 'page' records."""
    rows = await pool.fetch(
        "SELECT data_type, COUNT(*) as count FROM scraped_data "
        "WHERE job_id = $1 AND data_type != 'page' "
        "GROUP BY data_type ORDER BY count DESC",
        job_id,
    )
    return {row["data_type"]: row["count"] for row in rows}


async def cleanup_stale_data(pool: asyncpg.Pool) -> dict[str, int]:
    """Delete old data that no longer provides value.

    Policy:
    - 'page' records older than 7 days (raw HTML, only useful for debugging)
    - scraped_data from 'failed' jobs older than 30 days (no useful content)
    """
    pages_deleted = await pool.fetchval(
        "WITH deleted AS ("
        "  DELETE FROM scraped_data"
        "  WHERE data_type = 'page' AND scraped_at < NOW() - INTERVAL '7 days'"
        "  RETURNING 1"
        ") SELECT COUNT(*) FROM deleted"
    )
    failed_deleted = await pool.fetchval(
        "WITH deleted AS ("
        "  DELETE FROM scraped_data"
        "  WHERE job_id IN ("
        "    SELECT id FROM scrape_jobs WHERE status = 'failed'"
        "    AND completed_at < NOW() - INTERVAL '30 days'"
        "  )"
        "  RETURNING 1"
        ") SELECT COUNT(*) FROM deleted"
    )
    return {"pages": pages_deleted or 0, "failed_job_data": failed_deleted or 0}


async def get_scraped_data_by_domain(
    pool: asyncpg.Pool,
    domain: str,
    *,
    data_type: str | None = None,
    limit: int = 100,
) -> list[ScrapedData]:
    if data_type:
        rows = await pool.fetch(
            "SELECT * FROM scraped_data WHERE domain = $1 AND data_type = $2 "
            "ORDER BY scraped_at DESC LIMIT $3",
            domain,
            data_type,
            limit,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM scraped_data WHERE domain = $1 ORDER BY scraped_at DESC LIMIT $2",
            domain,
            limit,
        )
    return [_parse_row(row) for row in rows]
