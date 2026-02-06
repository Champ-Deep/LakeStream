from fastapi import APIRouter

from src.db.pool import get_pool
from src.models.api import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    db_status = "disconnected"
    redis_status = "disconnected"

    try:
        pool = await get_pool()
        result = await pool.fetchval("SELECT 1")
        if result == 1:
            db_status = "connected"
    except Exception:
        pass

    try:
        from arq.connections import create_pool as create_arq_pool

        from src.config.settings import get_settings

        settings = get_settings()
        from arq.connections import RedisSettings

        redis = await create_arq_pool(RedisSettings.from_dsn(settings.redis_url))
        await redis.ping()
        redis_status = "connected"
        await redis.aclose()
    except Exception:
        pass

    status = "ok" if db_status == "connected" else "degraded"
    return HealthResponse(status=status, database=db_status, redis=redis_status)
