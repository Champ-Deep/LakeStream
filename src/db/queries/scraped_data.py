import json
from uuid import UUID, uuid4

import asyncpg

from src.models.scraped_data import ScrapedData


async def insert_scraped_data(
    pool: asyncpg.Pool,
    *,
    job_id: UUID,
    domain: str,
    data_type: str,
    url: str | None = None,
    title: str | None = None,
    metadata: dict | None = None,
) -> UUID:
    record_id = uuid4()
    await pool.execute(
        """
        INSERT INTO scraped_data (id, job_id, domain, data_type, url, title, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
        """,
        record_id,
        job_id,
        domain,
        data_type,
        url,
        title,
        json.dumps(metadata or {}),
    )
    return record_id


async def batch_insert_scraped_data(
    pool: asyncpg.Pool,
    records: list[dict],
) -> int:
    """Insert multiple scraped_data records in a single batch."""
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
            )
        )

    await pool.executemany(
        """
        INSERT INTO scraped_data (id, job_id, domain, data_type, url, title, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
        """,
        values,
    )
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
