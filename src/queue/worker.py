from arq.connections import RedisSettings

from src.config.settings import get_settings
from src.queue.jobs import process_scrape_job


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

    _settings = get_settings()
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_jobs = _settings.max_concurrent_jobs
    job_timeout = 300
