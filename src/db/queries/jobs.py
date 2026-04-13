from datetime import datetime
from uuid import UUID, uuid4

import asyncpg

from src.models.job import JobStatus, ScrapeJob, ScrapeJobInput


async def create_job(
    pool: asyncpg.Pool,
    input: ScrapeJobInput,
    org_id: UUID | None = None,
    user_id: UUID | None = None,
) -> ScrapeJob:
    # Fall back to "Default Organization" for unauthenticated dashboard scrapes
    if org_id is None:
        org_id = await pool.fetchval("SELECT id FROM organizations WHERE slug = 'default'")

    try:
        row = await pool.fetchrow(
            """
            INSERT INTO scrape_jobs (
                id, domain, template_id, status, org_id, user_id,
                input_data_types, input_max_pages, input_tier_override, input_region
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING *
            """,
            uuid4(),
            input.domain,
            input.template_id or "auto",
            JobStatus.PENDING,
            org_id,
            user_id,
            input.data_types,
            input.max_pages,
            input.tier,
            input.region,
        )
    except asyncpg.exceptions.UndefinedColumnError:
        # Migration 023 not yet applied — fall back to original insert
        row = await pool.fetchrow(
            """
            INSERT INTO scrape_jobs (id, domain, template_id, status, org_id, user_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            uuid4(),
            input.domain,
            input.template_id or "auto",
            JobStatus.PENDING,
            org_id,
            user_id,
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


async def update_heartbeat(pool: asyncpg.Pool, job_id: UUID) -> None:
    """Update the heartbeat timestamp to signal the job is still active."""
    await pool.execute(
        "UPDATE scrape_jobs SET last_activity_at = NOW() WHERE id = $1",
        job_id,
    )


MAX_JOB_RETRIES = 2  # Stale jobs auto-restart up to this many times, then fail permanently


async def recover_stale_jobs(
    pool: asyncpg.Pool,
    stale_minutes: int = 10,
    redis_url: str | None = None,
) -> int:
    """Recover jobs stuck at 'running' with no recent heartbeat activity.

    Strategy (per job):
      - retry_count < MAX_JOB_RETRIES: re-queue the job and reset to PENDING
        (auto-restart when a worker slot opens)
      - retry_count >= MAX_JOB_RETRIES: mark as FAILED permanently

    Uses last_activity_at (heartbeat) when available, falling back to created_at.
    Fires pg_notify('job_status_changed', ...) via the DB trigger on each update.
    """
    # Fetch all stale running jobs first
    try:
        stale_rows = await pool.fetch(
            """
            SELECT id, domain, template_id, retry_count,
                   input_data_types, input_max_pages, input_tier_override, input_region
            FROM scrape_jobs
            WHERE status = 'running'
              AND COALESCE(last_activity_at, created_at)
                  < NOW() - INTERVAL '1 minute' * $1
            """,
            stale_minutes,
        )
        heartbeat_col_exists = True
    except asyncpg.exceptions.UndefinedColumnError:
        # Migration 020/023 not yet applied — fall back to created_at, no restart
        result = await pool.execute(
            """
            UPDATE scrape_jobs
            SET status = 'failed',
                error_message = 'Job timed out (stale, no restart available)',
                completed_at = NOW()
            WHERE status = 'running'
              AND created_at < NOW() - INTERVAL '1 minute' * $1
            """,
            stale_minutes,
        )
        return int(result.split()[-1])

    if not stale_rows:
        return 0

    restarted = 0
    failed = 0

    for row in stale_rows:
        job_id = row["id"]
        retry_count = row.get("retry_count", 0) or 0

        if retry_count < MAX_JOB_RETRIES:
            # Auto-restart: reset to PENDING and re-enqueue
            attempt = retry_count + 1
            await pool.execute(
                """
                UPDATE scrape_jobs
                SET status = 'pending',
                    retry_count = $2,
                    error_message = 'Stale job auto-restarted (attempt ' || $2 || ' of ' || $3 || ')',
                    last_activity_at = NULL,
                    completed_at = NULL
                WHERE id = $1
                """,
                job_id, attempt, MAX_JOB_RETRIES,
            )

            # Re-enqueue in arq if redis is available
            if redis_url:
                try:
                    from arq.connections import RedisSettings
                    from arq.connections import create_pool as create_arq_pool

                    _default_data_types = [
                        "blog_url", "article", "contact", "tech_stack", "resource", "pricing"
                    ]
                    redis = await create_arq_pool(RedisSettings.from_dsn(redis_url))
                    await redis.enqueue_job(
                        "process_scrape_job",
                        job_id=str(job_id),
                        domain=row["domain"],
                        template_id=row.get("template_id") or "auto",
                        max_pages=row.get("input_max_pages") or 100,
                        data_types=list(row.get("input_data_types") or _default_data_types),
                        tier=row.get("input_tier_override"),
                        region=row.get("input_region"),
                        _job_id=f"retry-{job_id}-{attempt}",  # unique arq key
                    )
                    await redis.aclose()
                except Exception:
                    pass  # Re-queue best-effort; job stays PENDING for next worker pickup

            restarted += 1
        else:
            # Permanently failed — too many stale retries
            await pool.execute(
                """
                UPDATE scrape_jobs
                SET status = 'failed',
                    error_message = 'Job permanently terminated after ' || $2
                        || ' stale restart attempts. Please submit a new job.',
                    completed_at = NOW()
                WHERE id = $1
                """,
                job_id, MAX_JOB_RETRIES,
            )
            failed += 1

    return restarted + failed


async def cancel_job(pool: asyncpg.Pool, job_id: UUID) -> bool:
    """Cancel a pending or running job. Returns True if the job was cancelled."""
    result = await pool.execute(
        "UPDATE scrape_jobs SET status = 'cancelled', "
        "error_message = 'Cancelled by user', completed_at = NOW() "
        "WHERE id = $1 AND status IN ('pending', 'running')",
        job_id,
    )
    return result != "UPDATE 0"


async def is_job_cancelled(pool: asyncpg.Pool, job_id: UUID) -> bool:
    """Check if a job has been cancelled (used for cooperative cancellation)."""
    status = await pool.fetchval(
        "SELECT status FROM scrape_jobs WHERE id = $1", job_id
    )
    return status == "cancelled"


async def list_jobs(
    pool: asyncpg.Pool,
    *,
    domain: str | None = None,
    status: str | None = None,
    user_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ScrapeJob]:
    conditions = []
    vals: list[object] = []
    idx = 1

    if domain:
        conditions.append(f"domain ILIKE ${idx}")
        vals.append(f"%{domain}%")
        idx += 1
    if status:
        conditions.append(f"status = ${idx}")
        vals.append(status)
        idx += 1
    if user_id:
        conditions.append(f"user_id = ${idx}")
        vals.append(user_id)
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    vals.extend([limit, offset])

    query = (
        f"SELECT * FROM scrape_jobs {where} ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx + 1}"
    )
    rows = await pool.fetch(query, *vals)
    return [ScrapeJob(**dict(row)) for row in rows]
