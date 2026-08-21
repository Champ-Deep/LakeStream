"""Queries for Tech Detect batch runs."""

import json
from uuid import UUID

import asyncpg

TERMINAL_STATUSES = ("completed", "failed", "cancelled")


async def create_run(
    pool: asyncpg.Pool,
    *,
    org_id: UUID,
    user_id: UUID | None,
    urls: list[str],
    source: str = "csv",
    options: dict | None = None,
    client_token: str | None = None,
) -> tuple[UUID, bool]:
    """Create a run plus its pending result rows.

    Returns (run_id, created). When `client_token` matches an existing run for
    this user, returns that run and False so a double submit is a no-op.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            if client_token and user_id:
                existing = await conn.fetchval(
                    "SELECT id FROM tech_detect_runs WHERE user_id = $1 AND client_token = $2",
                    user_id,
                    client_token,
                )
                if existing:
                    return existing, False

            run_id = await conn.fetchval(
                """INSERT INTO tech_detect_runs
                       (org_id, user_id, source, total, options, client_token, status)
                   VALUES ($1, $2, $3, $4, $5::jsonb, $6, 'pending')
                   RETURNING id""",
                org_id,
                user_id,
                source,
                len(urls),
                json.dumps(options or {}),
                client_token,
            )
            await conn.executemany(
                """INSERT INTO tech_detect_results (run_id, position, input_url, status)
                   VALUES ($1, $2, $3, 'pending')""",
                [(run_id, i, u) for i, u in enumerate(urls)],
            )
            return run_id, True


async def mark_running(pool: asyncpg.Pool, run_id: UUID) -> None:
    await pool.execute(
        """UPDATE tech_detect_runs
           SET status = 'running', heartbeat_at = NOW()
           WHERE id = $1 AND status = 'pending'""",
        run_id,
    )


async def heartbeat(pool: asyncpg.Pool, run_id: UUID) -> None:
    await pool.execute("UPDATE tech_detect_runs SET heartbeat_at = NOW() WHERE id = $1", run_id)


async def save_result(pool: asyncpg.Pool, run_id: UUID, position: int, result: dict) -> None:
    """Persist one URL's outcome and advance the run counters atomically.

    Counters are incremented in SQL rather than read-modify-write in Python so
    concurrent workers can never lose an increment.
    """
    status = result.get("status", "failed")
    payload = {
        "detections": result.get("detections", []),
        "by_category": result.get("by_category", {}),
        "total": result.get("total", 0),
        "recommended": result.get("recommended", 0),
        "escalated": result.get("escalated", False),
        "truncated": result.get("truncated", False),
        "fields": result.get("fields", {}),
    }
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """UPDATE tech_detect_results
                   SET status = $3, final_url = $4, domain = $5, http_status = $6,
                       error = $7, detect = $8::jsonb, duration_ms = $9, completed_at = NOW()
                   WHERE run_id = $1 AND position = $2""",
                run_id,
                position,
                status,
                result.get("final_url"),
                result.get("domain"),
                result.get("http_status"),
                result.get("error") or None,
                json.dumps(payload, default=str),
                result.get("duration_ms", 0),
            )
            await conn.execute(
                f"""UPDATE tech_detect_runs
                    SET done_count = done_count + 1,
                        {_counter_column(status)} = {_counter_column(status)} + 1,
                        heartbeat_at = NOW()
                    WHERE id = $1""",
                run_id,
            )


def _counter_column(status: str) -> str:
    return {
        "ok": "ok_count",
        "blocked": "blocked_count",
        "skipped": "skipped_count",
    }.get(status, "failed_count")


async def finish_run(pool: asyncpg.Pool, run_id: UUID, error: str | None = None) -> None:
    await pool.execute(
        """UPDATE tech_detect_runs
           SET status = $2, error_message = $3, completed_at = NOW()
           WHERE id = $1""",
        run_id,
        "failed" if error else "completed",
        error,
    )


async def get_run(pool: asyncpg.Pool, run_id: UUID) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM tech_detect_runs WHERE id = $1", run_id)
    return dict(row) if row else None


async def get_results(pool: asyncpg.Pool, run_id: UUID) -> list[dict]:
    rows = await pool.fetch(
        """SELECT position, input_url, final_url, domain, status, http_status,
                  error, detect, duration_ms, completed_at
           FROM tech_detect_results WHERE run_id = $1 ORDER BY position""",
        run_id,
    )
    out = []
    for r in rows:
        d = dict(r)
        detect = d.get("detect")
        d["detect"] = json.loads(detect) if isinstance(detect, str) else (detect or {})
        out.append(d)
    return out


async def get_pending_urls(pool: asyncpg.Pool, run_id: UUID) -> list[tuple[int, str]]:
    """Positions still to process — lets a resumed run skip finished work."""
    rows = await pool.fetch(
        """SELECT position, input_url FROM tech_detect_results
           WHERE run_id = $1 AND status = 'pending' ORDER BY position""",
        run_id,
    )
    return [(r["position"], r["input_url"]) for r in rows]


async def list_runs(
    pool: asyncpg.Pool, *, user_id: UUID | None, org_id: UUID, limit: int = 10
) -> list[dict]:
    if user_id:
        rows = await pool.fetch(
            """SELECT id, source, total, done_count, ok_count, blocked_count,
                      failed_count, skipped_count, status, created_at, completed_at
               FROM tech_detect_runs WHERE user_id = $1
               ORDER BY created_at DESC LIMIT $2""",
            user_id,
            limit,
        )
    else:
        rows = await pool.fetch(
            """SELECT id, source, total, done_count, ok_count, blocked_count,
                      failed_count, skipped_count, status, created_at, completed_at
               FROM tech_detect_runs WHERE org_id = $1
               ORDER BY created_at DESC LIMIT $2""",
            org_id,
            limit,
        )
    return [dict(r) for r in rows]


async def recover_orphan_runs(pool: asyncpg.Pool, stale_minutes: int = 10) -> int:
    """Fail runs whose worker died mid-flight, so they stop showing as running."""
    rows = await pool.fetch(
        f"""UPDATE tech_detect_runs
            SET status = 'failed',
                error_message = COALESCE(error_message,
                    'Worker stopped responding; partial results retained'),
                completed_at = NOW()
            WHERE status IN ('pending', 'running')
              AND COALESCE(heartbeat_at, created_at) < NOW() - INTERVAL '{stale_minutes} minutes'
            RETURNING id""",
    )
    return len(rows)
