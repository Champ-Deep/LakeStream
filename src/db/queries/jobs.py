from datetime import datetime
from uuid import UUID, uuid4

import asyncpg

from src.models.job import JobStatus, ScrapeJob, ScrapeJobInput


async def create_job(pool: asyncpg.Pool, input: ScrapeJobInput) -> ScrapeJob:
    row = await pool.fetchrow(
        """
        INSERT INTO scrape_jobs (id, domain, template_id, status)
        VALUES ($1, $2, $3, $4)
        RETURNING *
        """,
        uuid4(),
        input.domain,
        input.template_id or "auto",
        JobStatus.PENDING,
    )
    assert row is not None
    return ScrapeJob(**dict(row))


async def get_job(pool: asyncpg.Pool, job_id: UUID) -> ScrapeJob | None:
    row = await pool.fetchrow("SELECT * FROM scrape_jobs WHERE id = $1", job_id)
    if row is None:
        return None
    return ScrapeJob(**dict(row))


async def update_job_status(
    pool: asyncpg.Pool,
    job_id: UUID,
    status: JobStatus,
    *,
    strategy_used: str | None = None,
    error_message: str | None = None,
    cost_usd: float | None = None,
    duration_ms: int | None = None,
    pages_scraped: int | None = None,
    completed_at: datetime | None = None,
) -> None:
    sets = ["status = $2"]
    vals: list[object] = [job_id, status]
    idx = 3

    for field, value in [
        ("strategy_used", strategy_used),
        ("error_message", error_message),
        ("cost_usd", cost_usd),
        ("duration_ms", duration_ms),
        ("pages_scraped", pages_scraped),
        ("completed_at", completed_at),
    ]:
        if value is not None:
            sets.append(f"{field} = ${idx}")
            vals.append(value)
            idx += 1

    query = f"UPDATE scrape_jobs SET {', '.join(sets)} WHERE id = $1"
    await pool.execute(query, *vals)


async def list_jobs(
    pool: asyncpg.Pool,
    *,
    domain: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ScrapeJob]:
    conditions = []
    vals: list[object] = []
    idx = 1

    if domain:
        conditions.append(f"domain = ${idx}")
        vals.append(domain)
        idx += 1
    if status:
        conditions.append(f"status = ${idx}")
        vals.append(status)
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    vals.extend([limit, offset])

    query = (
        f"SELECT * FROM scrape_jobs {where} "
        f"ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx + 1}"
    )
    rows = await pool.fetch(query, *vals)
    return [ScrapeJob(**dict(row)) for row in rows]
