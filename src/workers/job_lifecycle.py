"""Job lifecycle helpers: cooperative cancellation polling and heartbeating.

Extracted from ``ContentWorker``, which previously had two near-identical
copies of each block inline in its Phase 1 and Phase 2 URL loops. This module
owns exactly those two concerns so they can be unit tested in isolation and
reused from a single call site per loop iteration.

Two heartbeat throttling strategies are provided because callers have two
different natural cadences:

- ``maybe_heartbeat`` throttles by a count of processed items (used by
  ``ContentWorker``'s per-URL loops, where "every N URLs" is the meaningful
  unit of progress).
- ``heartbeat`` throttles by wall-clock time, at most once per
  ``min_interval_seconds`` (default matches ``BaseWorker.heartbeat``'s
  30-second interval). This suits callers like ``DomainMapperWorker`` that
  don't have a per-item loop to count against but still want to avoid
  hammering the DB if called frequently over a long-running operation.
"""

import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog

log = structlog.get_logger()

# Signature for the DB check used to see if a job was cancelled.
CancelCheck = Callable[[object, UUID], Awaitable[bool]]
# Signature for the DB call used to record a heartbeat.
HeartbeatFn = Callable[[object, UUID], Awaitable[None]]

# Matches BaseWorker's _HEARTBEAT_INTERVAL: minimum seconds between
# time-throttled heartbeat DB writes.
DEFAULT_HEARTBEAT_MIN_INTERVAL_SECONDS = 30


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
        heartbeat_min_interval_seconds: float = DEFAULT_HEARTBEAT_MIN_INTERVAL_SECONDS,
        cancel_check: CancelCheck = _default_is_job_cancelled,
        heartbeat_fn: HeartbeatFn = _default_update_heartbeat,
        logger: Any | None = None,
    ) -> None:
        self._pool = pool
        self._job_id = UUID(job_id) if isinstance(job_id, str) else job_id
        self._heartbeat_every = heartbeat_every
        self._heartbeat_min_interval_seconds = heartbeat_min_interval_seconds
        self._cancel_check = cancel_check
        self._heartbeat_fn = heartbeat_fn
        self._log = logger or log
        self._last_heartbeat_at: float = 0.0

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

    async def heartbeat(self) -> None:
        """Send a heartbeat at most once per ``heartbeat_min_interval_seconds``.

        Time-throttled variant of ``maybe_heartbeat``, for callers without a
        natural per-item counter (e.g. a single long-running operation rather
        than a loop over URLs). Mirrors ``BaseWorker.heartbeat``'s throttling
        semantics exactly so both worker families behave the same way under
        the stale-job recovery cron.
        """
        if not self._pool:
            return
        now = time.time()
        if now - self._last_heartbeat_at < self._heartbeat_min_interval_seconds:
            return
        self._last_heartbeat_at = now
        try:
            await self._heartbeat_fn(self._pool, self._job_id)
        except Exception as e:
            self._log.warning("heartbeat_failed", error=str(e))
