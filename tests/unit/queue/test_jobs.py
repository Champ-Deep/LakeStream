"""Unit tests for src/queue/jobs.py — plan.md S3.1 / MED-1.

Focuses on the *error paths* in process_scrape_job:
  - asyncio.TimeoutError → job marked FAILED with the timeout message
  - Generic Exception → job marked FAILED + re-raised so arq retries it
  - Heartbeat background task is started and cancelled cleanly

We don't try to exercise the happy path here — that requires a real DB
+ Redis + Playwright + arq context, which lives in tests/integration/.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.job import JobStatus


def _ctx_with_pool() -> dict:
    """Build a minimal arq ctx dict with a mocked pool.

    The pool needs fetchval (for org proxy lookup), fetchrow, execute,
    and is consumed by db.queries.jobs which we patch out anyway.
    """
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=None)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="UPDATE 1")
    return {"pool": pool}


@pytest.mark.asyncio
async def test_timeout_marks_job_failed_with_timeout_message():
    """If the inner work raises asyncio.TimeoutError (from asyncio.timeout),
    the job is marked FAILED with the canonical 'timed out' message.
    """
    from src.queue import jobs as jobs_mod

    job_id = str(uuid4())
    job_record = MagicMock()
    job_record.org_id = None
    job_record.user_id = None

    # DomainMapperWorker.execute raises TimeoutError to simulate the
    # asyncio.timeout window expiring.
    mapper_inst = MagicMock()
    mapper_inst.execute = AsyncMock(side_effect=asyncio.TimeoutError())
    mapper_class = MagicMock(return_value=mapper_inst)

    with (
        patch.object(jobs_mod.job_queries, "update_job_status", new_callable=AsyncMock) as mock_update,
        patch.object(jobs_mod.job_queries, "get_job", new_callable=AsyncMock, return_value=job_record),
        patch.object(jobs_mod.job_queries, "update_heartbeat", new_callable=AsyncMock),
        patch("src.workers.domain_mapper.DomainMapperWorker", mapper_class),
    ):
        result = await jobs_mod.process_scrape_job(
            _ctx_with_pool(),
            job_id=job_id,
            domain="example.com",
            template_id="auto",
            max_pages=1,
            data_types=["article"],
        )

    # First call sets status to RUNNING; the timeout-handler call below
    # marks it FAILED. Find that call.
    failed_calls = [
        c for c in mock_update.call_args_list
        if len(c.args) >= 3 and c.args[2] == JobStatus.FAILED
    ]
    assert failed_calls, "expected an update_job_status(..., FAILED) call"
    failed_call = failed_calls[-1]
    assert "timed out" in failed_call.kwargs["error_message"].lower()
    # Returned dict signals failure to arq
    assert result["data_extracted"] == 0
    assert any("timed out" in e.lower() for e in result["errors"])


@pytest.mark.asyncio
async def test_generic_exception_marks_failed_and_reraises():
    """A non-timeout exception in the inner work must:
       1. Update the row to FAILED with the exception message.
       2. Re-raise so arq can apply its retry policy.
    """
    from src.queue import jobs as jobs_mod

    job_id = str(uuid4())
    job_record = MagicMock()
    job_record.org_id = None
    job_record.user_id = None

    mapper_inst = MagicMock()
    mapper_inst.execute = AsyncMock(side_effect=RuntimeError("boom"))
    mapper_class = MagicMock(return_value=mapper_inst)

    with (
        patch.object(jobs_mod.job_queries, "update_job_status", new_callable=AsyncMock) as mock_update,
        patch.object(jobs_mod.job_queries, "get_job", new_callable=AsyncMock, return_value=job_record),
        patch.object(jobs_mod.job_queries, "update_heartbeat", new_callable=AsyncMock),
        patch("src.workers.domain_mapper.DomainMapperWorker", mapper_class),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            await jobs_mod.process_scrape_job(
                _ctx_with_pool(),
                job_id=job_id,
                domain="example.com",
                template_id="auto",
                max_pages=1,
                data_types=["article"],
            )

    failed_calls = [
        c for c in mock_update.call_args_list
        if len(c.args) >= 3 and c.args[2] == JobStatus.FAILED
    ]
    assert failed_calls, "expected an update_job_status(..., FAILED) call"
    assert "boom" in failed_calls[-1].kwargs["error_message"]


@pytest.mark.asyncio
async def test_heartbeat_task_is_cleaned_up_on_failure_path():
    """When the inner work fails fast, we still want the heartbeat task
    cancelled cleanly in the finally block — no leaked asyncio tasks.
    """
    from src.queue import jobs as jobs_mod

    job_id = str(uuid4())
    job_record = MagicMock()
    job_record.org_id = None
    job_record.user_id = None

    mapper_inst = MagicMock()
    mapper_inst.execute = AsyncMock(side_effect=RuntimeError("early"))
    mapper_class = MagicMock(return_value=mapper_inst)

    tasks_before = {t for t in asyncio.all_tasks() if not t.done()}

    with (
        patch.object(jobs_mod.job_queries, "update_job_status", new_callable=AsyncMock),
        patch.object(jobs_mod.job_queries, "get_job", new_callable=AsyncMock, return_value=job_record),
        patch.object(jobs_mod.job_queries, "update_heartbeat", new_callable=AsyncMock),
        patch("src.workers.domain_mapper.DomainMapperWorker", mapper_class),
    ):
        with pytest.raises(RuntimeError):
            await jobs_mod.process_scrape_job(
                _ctx_with_pool(),
                job_id=job_id,
                domain="example.com",
                template_id="auto",
                max_pages=1,
                data_types=["article"],
            )

    # Give the event loop one tick to let the cancelled heartbeat task settle.
    await asyncio.sleep(0)

    # No new pending tasks should remain after process_scrape_job returns.
    leaked = {
        t for t in asyncio.all_tasks() if not t.done()
    } - tasks_before - {asyncio.current_task()}
    assert not leaked, f"heartbeat task leaked: {leaked!r}"


def test_job_hard_timeout_constant_below_arq_default():
    """Sanity: our 90-min timeout must fire before arq's 2-hour kill so
    failed jobs get a clean 'timed out' message instead of an opaque
    arq termination.
    """
    from src.queue.jobs import JOB_HARD_TIMEOUT_SECONDS

    arq_default_seconds = 7200  # 2 hours
    assert JOB_HARD_TIMEOUT_SECONDS < arq_default_seconds
    assert JOB_HARD_TIMEOUT_SECONDS >= 1800  # at least 30 min — long crawls
