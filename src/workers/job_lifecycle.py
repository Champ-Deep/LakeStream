"""Job lifecycle helpers: cooperative cancellation polling and heartbeating.

Extracted from ``ContentWorker``, which previously had two near-identical
copies of each block inline in its Phase 1 and Phase 2 URL loops. This module
owns exactly those two concerns so they can be unit tested in isolation and
reused from a single call site per loop iteration.
"""

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog

log = structlog.get_logger()

# Signature for the DB check used to see if a job was cancelled.
CancelCheck = Callable[[object, UUID], Awaitable[bool]]
# Signature for the DB call used to record a heartbeat.
HeartbeatFn = Callable[[object, UUID], Awaitable[None]]


async def _default_is_job_cancelled(pool: object, job_id: UUID) -> bool:
    from src.db.queries.jobs import is_job_cancelled

    return await is_job_cancelled(pool, job_id)


async def _default_update_heartbeat(pool: object, job_id: UUID) -> None:
    from src.db.queries.jobs import update_heartbeat

    await update_heartbeat(pool, job_id)


class JobLifecycle:
    """Owns cancellation-polling and heartbeat side effects for a single job.

    Both checks are no-ops when no DB pool is configured (e.g. in tests or
    ephemeral/raw-only runs), matching the previous inline ``if self._pool``
    guards in ``ContentWorker``.

    The DB calls are injectable (``cancel_check`` / ``heartbeat_fn``) so unit
    tests can exercise the polling/heartbeat cadence without a real database
    — this mirrors ChampScrape's pattern of injecting a fake clock/pollable
    check for deterministic tests.
    """

    def __init__(
        self,
        pool: object | None,
        job_id: str,
        *,
        heartbeat_every: int = 5,
        cancel_check: CancelCheck = _default_is_job_cancelled,
        heartbeat_fn: HeartbeatFn = _default_update_heartbeat,
        logger: Any | None = None,
    ) -> None:
        self._pool = pool
        self._job_id = UUID(job_id) if isinstance(job_id, str) else job_id
        self._heartbeat_every = heartbeat_every
        self._cancel_check = cancel_check
        self._heartbeat_fn = heartbeat_fn
        self._log = logger or log

    async def is_cancelled(self, *, urls_processed: int = 0, phase: str = "") -> bool:
        """Return True if the job has been cancelled by the user.

        Swallows and logs errors from the underlying check — a failed
        cancellation check should never abort the job itself.
        """
        if not self._pool:
            return False
        try:
            if await self._cancel_check(self._pool, self._job_id):
                event = "job_cancelled_by_user" + (f"_{phase}" if phase else "")
                self._log.info(event, urls_processed=urls_processed)
                return True
            return False
        except Exception as e:
            self._log.warning("cancel_check_failed", error=str(e))
            return False

    async def maybe_heartbeat(self, urls_processed: int) -> None:
        """Send a heartbeat every ``heartbeat_every`` processed URLs.

        Non-critical — failures are logged, never raised, so heartbeat
        problems can't fail the job.
        """
        if not self._pool:
            return
        if self._heartbeat_every <= 0 or urls_processed % self._heartbeat_every != 0:
            return
        try:
            await self._heartbeat_fn(self._pool, self._job_id)
        except Exception as e:
            self._log.warning("heartbeat_failed", error=str(e))
