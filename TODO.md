# TODO / Follow-ups

## Scrape job cancellation — finalize state transition

**Where:** `src/queue/jobs.py` (after `ContentWorker.execute()` returns)

**Problem:** When a user cancels a running job, `ContentWorker` exits early (cooperative cancel check).
The orchestrator then marks the job `completed` or `failed` based on row count — it never
marks it `cancelled`. The UI cancel button stops the worker but the job row doesn't reflect it.

**Fix sketch:** After `ContentWorker` returns, check `is_job_cancelled(pool, uid)` and, if true,
call `update_job_status(pool, uid, JobStatus.CANCELLED, ...)` before the
`completed`/`failed` branch. See `src/db/queries/jobs.py` for the helpers.

**Context:** Discovered while reviewing PR `fix/scrape-cancel-and-complete-actions`
(that PR fixes cancel reliability in the worker + UI action-button auto-refresh,
but leaves the final-state transition gap unresolved).
