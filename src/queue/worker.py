import structlog
from arq.connections import RedisSettings
from arq.cron import cron

from src.config.settings import get_settings
from src.queue.discover_jobs import check_tracked_searches, process_discovery_job
from src.queue.jobs import process_scrape_job
from src.workers.scheduled_scraper import check_scheduled_scrapes
from src.workers.signal_processor import process_signals

log = structlog.get_logger()


async def startup(ctx: dict) -> None:
    from src.db.pool import get_pool
    from src.db.queries.jobs import recover_stale_jobs
    from src.utils.logger import setup_logging

    setup_logging()
    ctx["pool"] = await get_pool()

    # Recover jobs stuck from previous worker crashes / container restarts
    count = await recover_stale_jobs(ctx["pool"])
    if count:
        log.info("recovered_stale_jobs", count=count)


async def recover_stale_jobs_cron(ctx: dict) -> None:
    """Cron: mark jobs stuck at 'running' as failed."""
    from src.db.queries.jobs import recover_stale_jobs

    count = await recover_stale_jobs(ctx["pool"])
    if count:
        log.info("recovered_stale_jobs_cron", count=count)


async def cleanup_stale_data_cron(ctx: dict) -> None:
    """Cron: delete old 'page' records (7d) and failed job data (30d)."""
    from src.db.queries.scraped_data import cleanup_stale_data

    result = await cleanup_stale_data(ctx["pool"])
    total = result["pages"] + result["failed_job_data"]
    if total:
        log.info("cleanup_stale_data", **result)


async def shutdown(ctx: dict) -> None:
    from src.db.pool import close_pool

    await close_pool()


class WorkerSettings:
    functions = [process_scrape_job, process_discovery_job]
    on_startup = startup
    on_shutdown = shutdown

    # Run scheduled scrape checks every hour at :00
    # Run signal evaluation every 15 minutes
    # Run tracked search checks every 15 minutes (offset by 10 min)
    # Run data retention cleanup daily at 3:00 AM
    cron_jobs = [
        cron(check_scheduled_scrapes, hour=None, minute=0),
        cron(process_signals, hour=None, minute={0, 15, 30, 45}),
        cron(check_tracked_searches, hour=None, minute={10, 25, 40, 55}),
        cron(recover_stale_jobs_cron, hour=None, minute={5, 20, 35, 50}),
        cron(cleanup_stale_data_cron, hour=3, minute=0),
    ]

    _settings = get_settings()
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_jobs = _settings.max_concurrent_jobs
    job_timeout = 7200  # 2 hours — heartbeat stale recovery (10 min) is the real safety net
