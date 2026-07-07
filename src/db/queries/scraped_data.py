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
                json.dumps(rec.get("metadata", {}), default=str),
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


def _build_search_conditions(
    *,
    user_id: UUID | None,
    domain: str | None,
    data_type: str | None,
    q: str | None,
    hide_pages_by_default: bool,
) -> tuple[list[str], list[object]]:
    """Build parameterized WHERE conditions shared by results-browsing and CSV export.

    Returns (conditions, values) where values are positionally ordered to line
    up with $1, $2, ... placeholders built from `conditions`.
    """
    conditions: list[str] = []
    vals: list[object] = []

    if user_id:
        conditions.append(f"user_id = ${len(vals) + 1}")
        vals.append(user_id)
    if domain:
        conditions.append(f"domain = ${len(vals) + 1}")
        vals.append(domain)
    if data_type:
        conditions.append(f"data_type = ${len(vals) + 1}")
        vals.append(data_type)
    elif hide_pages_by_default and not (q and q.strip()):
        # Default view: hide raw page records (they clutter useful results).
        # Callers can still see them by explicitly passing data_type="page".
        conditions.append("data_type != 'page'")
    if q and q.strip():
        # Deep search: search title, URL, domain AND inside metadata JSONB content.
        # metadata::text casts the entire JSON to a text string for ILIKE matching,
        # so keywords in article body, contact names, descriptions etc. are found.
        idx = len(vals) + 1
        conditions.append(
            f"(title ILIKE ${idx} OR url ILIKE ${idx} "
            f"OR domain ILIKE ${idx} OR metadata::text ILIKE ${idx})"
        )
        vals.append(f"%{q.strip()}%")

    return conditions, vals


async def search_scraped_data(
    pool: asyncpg.Pool,
    *,
    user_id: UUID | None = None,
    domain: str | None = None,
    data_type: str | None = None,
    q: str | None = None,
    hide_pages_by_default: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[ScrapedData]:
    """Search scraped_data with optional user/domain/data_type/keyword filters.

    When `q` is set, results are ranked by relevance (title match, then URL
    match, then domain match, then content match) before falling back to
    recency. Otherwise results are ordered by scraped_at DESC.

    `hide_pages_by_default` reproduces the /results browsing behavior of
    hiding raw 'page' records unless a data_type or keyword filter is active;
    CSV export callers leave this False to export exactly what was asked for.
    """
    conditions, vals = _build_search_conditions(
        user_id=user_id,
        domain=domain,
        data_type=data_type,
        q=q,
        hide_pages_by_default=hide_pages_by_default,
    )
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    if q and q.strip():
        q_idx = len(vals)  # 1-based index of the keyword param already appended
        order_clause = (
            "ORDER BY "
            f"CASE WHEN title ILIKE ${q_idx} THEN 0 "
            f"WHEN url ILIKE ${q_idx} THEN 1 "
            f"WHEN domain ILIKE ${q_idx} THEN 2 "
            "ELSE 3 END, "
            "scraped_at DESC"
        )
    else:
        order_clause = "ORDER BY scraped_at DESC"

    query_vals = [*vals, limit, offset]
    query = (
        f"SELECT * FROM scraped_data {where} "
        f"{order_clause} LIMIT ${len(vals) + 1} OFFSET ${len(vals) + 2}"
    )
    rows = await pool.fetch(query, *query_vals)
    return [_parse_row(row) for row in rows]


async def count_search_scraped_data(
    pool: asyncpg.Pool,
    *,
    user_id: UUID | None = None,
    domain: str | None = None,
    data_type: str | None = None,
    q: str | None = None,
    hide_pages_by_default: bool = False,
) -> int:
    """Count rows matching the same filters as search_scraped_data (no limit/offset)."""
    conditions, vals = _build_search_conditions(
        user_id=user_id,
        domain=domain,
        data_type=data_type,
        q=q,
        hide_pages_by_default=hide_pages_by_default,
    )
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"SELECT COUNT(*) FROM scraped_data {where}"
    if vals:
        total = await pool.fetchval(query, *vals)
    else:
        total = await pool.fetchval(query)
    return total or 0


async def count_distinct_domains(pool: asyncpg.Pool, *, user_id: UUID | None = None) -> int:
    """Count distinct domains present in scraped_data, optionally scoped to a user."""
    if user_id:
        count = await pool.fetchval(
            "SELECT COUNT(DISTINCT domain) FROM scraped_data WHERE user_id = $1", user_id
        )
    else:
        count = await pool.fetchval("SELECT COUNT(DISTINCT domain) FROM scraped_data")
    return count or 0


async def list_distinct_domains(pool: asyncpg.Pool, *, user_id: UUID | None = None) -> list[str]:
    """List distinct domains present in scraped_data, optionally scoped to a user."""
    if user_id:
        rows = await pool.fetch(
            "SELECT DISTINCT domain FROM scraped_data WHERE user_id = $1 ORDER BY domain",
            user_id,
        )
    else:
        rows = await pool.fetch("SELECT DISTINCT domain FROM scraped_data ORDER BY domain")
    return [row["domain"] for row in rows]


async def count_by_data_type(pool: asyncpg.Pool, *, user_id: UUID | None = None) -> list[dict]:
    """Return per-data_type row counts, optionally scoped to a user.

    Returns a list of {"data_type": str, "cnt": int} ordered by count descending.
    """
    if user_id:
        rows = await pool.fetch(
            "SELECT data_type, COUNT(*) AS cnt FROM scraped_data "
            "WHERE user_id = $1 GROUP BY data_type ORDER BY cnt DESC",
            user_id,
        )
    else:
        rows = await pool.fetch(
            "SELECT data_type, COUNT(*) AS cnt FROM scraped_data "
            "GROUP BY data_type ORDER BY cnt DESC"
        )
    return [{"data_type": row["data_type"], "cnt": row["cnt"]} for row in rows]


async def get_data_type_breakdown_for_domain(
    pool: asyncpg.Pool,
    domain: str,
    *,
    user_id: UUID | None = None,
) -> dict[str, int]:
    """Return {data_type: count} for a domain, optionally scoped to a user."""
    if user_id:
        rows = await pool.fetch(
            """
            SELECT data_type, COUNT(*) as count
            FROM scraped_data
            WHERE domain = $1 AND user_id = $2
            GROUP BY data_type
            """,
            domain,
            user_id,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT data_type, COUNT(*) as count
            FROM scraped_data
            WHERE domain = $1
            GROUP BY data_type
            """,
            domain,
        )
    return {row["data_type"]: row["count"] for row in rows}


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
