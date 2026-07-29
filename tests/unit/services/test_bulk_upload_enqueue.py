import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.models.job import JobStatus, ScrapeJob
from src.services.bulk_upload import STAGGER_DELAY_SECONDS, enqueue_bulk_jobs


def _make_job(domain: str) -> ScrapeJob:
    return ScrapeJob(
        id=uuid4(),
        domain=domain,
        template_id="auto",
        status=JobStatus.PENDING,
        created_at=datetime.now(UTC),
    )


class TestEnqueueBulkJobsStagger:
    @pytest.mark.asyncio
    async def test_custom_stagger_seconds_used_for_defer(self):
        """defer_seconds should scale with the explicit stagger_seconds argument,
        not the module-level STAGGER_DELAY_SECONDS default."""
        domains = ["a.com", "b.com", "c.com"]
        redis_mock = AsyncMock()

        with (
            patch(
                "arq.connections.create_pool", new_callable=AsyncMock, return_value=redis_mock
            ),
            patch(
                "src.db.queries.jobs.create_job",
                new_callable=AsyncMock,
                side_effect=[_make_job(d) for d in domains],
            ),
        ):
            results = await enqueue_bulk_jobs(
                pool=AsyncMock(),
                domains=domains,
                org_id=uuid4(),
                user_id=uuid4(),
                stagger_seconds=7,
            )

        assert [r["defer_seconds"] for r in results] == [0, 7, 14]
        assert all(r["status"] == "queued" for r in results)

    @pytest.mark.asyncio
    async def test_default_stagger_seconds_matches_module_constant(self):
        domains = ["a.com", "b.com"]
        redis_mock = AsyncMock()

        with (
            patch(
                "arq.connections.create_pool", new_callable=AsyncMock, return_value=redis_mock
            ),
            patch(
                "src.db.queries.jobs.create_job",
                new_callable=AsyncMock,
                side_effect=[_make_job(d) for d in domains],
            ),
        ):
            results = await enqueue_bulk_jobs(
                pool=AsyncMock(),
                domains=domains,
                org_id=uuid4(),
                user_id=uuid4(),
            )

        assert [r["defer_seconds"] for r in results] == [0, STAGGER_DELAY_SECONDS]

    @pytest.mark.asyncio
    async def test_concurrent_calls_with_different_stagger_do_not_interfere(self):
        """Regression test for the fixed concurrency bug: two concurrent uploads
        with different stagger_seconds must not clobber each other's delay,
        since stagger_seconds is now a plain function argument instead of
        mutated module-level state."""
        redis_mock = AsyncMock()

        async def run(stagger: int, domains: list[str]):
            with patch(
                "src.db.queries.jobs.create_job",
                new_callable=AsyncMock,
                side_effect=[_make_job(d) for d in domains],
            ):
                return await enqueue_bulk_jobs(
                    pool=AsyncMock(),
                    domains=domains,
                    org_id=uuid4(),
                    user_id=uuid4(),
                    stagger_seconds=stagger,
                )

        with patch(
            "arq.connections.create_pool", new_callable=AsyncMock, return_value=redis_mock
        ):
            results_a, results_b = await asyncio.gather(
                run(5, ["a1.com", "a2.com"]),
                run(50, ["b1.com", "b2.com"]),
            )

        assert [r["defer_seconds"] for r in results_a] == [0, 5]
        assert [r["defer_seconds"] for r in results_b] == [0, 50]
