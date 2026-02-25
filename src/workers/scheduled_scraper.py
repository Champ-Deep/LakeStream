"""arq cron job for automatically scraping tracked domains on schedule."""

import structlog

log = structlog.get_logger()


async def check_scheduled_scrapes(ctx: dict) -> None:
    """Check for tracked domains due for scraping and enqueue jobs."""
    from src.db.pool import get_pool
    from src.db.queries.jobs import create_job
    from src.db.queries.tracked_domains import get_due_domains, mark_scraped
    from src.models.job import ScrapeJobInput

    pool = await get_pool()
    due = await get_due_domains(pool)

    if not due:
        return

    log.info("scheduled_scrape_check", due_count=len(due))

    for tracked in due:
        try:
            job_input = ScrapeJobInput(
                domain=tracked.domain,
                data_types=tracked.data_types,
                max_pages=tracked.max_pages,
                template_id=tracked.template_id if tracked.template_id != "auto" else None,
            )
            job = await create_job(pool, job_input)

            # Enqueue to arq worker
            redis = ctx.get("redis")
            if redis:
                await redis.enqueue_job(
                    "process_scrape_job",
                    job_id=str(job.id),
                    domain=tracked.domain,
                    template_id=tracked.template_id or "auto",
                    max_pages=tracked.max_pages,
                    data_types=tracked.data_types,
                )

            await mark_scraped(pool, tracked.domain)
            log.info(
                "scheduled_scrape_enqueued",
                domain=tracked.domain,
                job_id=str(job.id),
            )
        except Exception:
            log.exception("scheduled_scrape_failed", domain=tracked.domain)
