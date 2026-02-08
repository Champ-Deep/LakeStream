from arq.connections import RedisSettings
from arq.cron import cron

from src.config.settings import get_settings
from src.queue.jobs import process_scrape_job
from src.workers.scheduled_scraper import check_scheduled_scrapes
from src.workers.signal_processor import process_signals


async def startup(ctx: dict) -> None:
    from src.db.pool import get_pool
    from src.utils.logger import setup_logging

    setup_logging()
    ctx["pool"] = await get_pool()


async def shutdown(ctx: dict) -> None:
    from src.db.pool import close_pool

    await close_pool()


class WorkerSettings:
    functions = [process_scrape_job]
    on_startup = startup
    on_shutdown = shutdown

    # Run scheduled scrape checks every hour at :00
    # Run signal evaluation every 15 minutes
    cron_jobs = [
        cron(check_scheduled_scrapes, hour=None, minute=0),
        cron(process_signals, hour=None, minute={0, 15, 30, 45}),
    ]

    _settings = get_settings()
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_jobs = _settings.max_concurrent_jobs
    job_timeout = 300
