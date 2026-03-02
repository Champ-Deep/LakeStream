import structlog
from arq.connections import RedisSettings
from arq.connections import create_pool as create_arq_pool
from fastapi import APIRouter

from src.config.settings import get_settings
from src.db.pool import get_pool
from src.models.api import HealthResponse
from src.services.lakecurrent import LakeCurrentClient

router = APIRouter()
log = structlog.get_logger()


@router.get("/ping")
async def ping() -> dict:
    """Liveness probe — instant 200, no external calls. Use for Railway healthcheck."""
    return {"status": "ok"}


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    db_status = "disconnected"
    redis_status = "disconnected"
    lakecurrent_status = "disabled"
    settings = get_settings()

    try:
        pool = await get_pool()
        result = await pool.fetchval("SELECT 1")
        if result == 1:
            db_status = "connected"
    except Exception as exc:
        log.warning("health_check_database_error", error=str(exc), error_type=type(exc).__name__)

    try:
        redis = await create_arq_pool(RedisSettings.from_dsn(settings.redis_url))
        await redis.ping()
        redis_status = "connected"
        await redis.aclose()
    except Exception as exc:
        log.warning("health_check_redis_error", error=str(exc), error_type=type(exc).__name__)

    # LakeCurrent health check
    try:
        if settings.lakecurrent_enabled:
            client = LakeCurrentClient(
                base_url=settings.lakecurrent_base_url,
                timeout=settings.lakecurrent_timeout,
            )
            try:
                result = await client.health()
                lakecurrent_status = result.get("status", "unknown")
            except Exception as exc:
                lakecurrent_status = "unreachable"
                log.warning(
                    "health_check_lakecurrent_unreachable",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            finally:
                await client.close()
    except Exception as exc:
        log.warning("health_check_lakecurrent_error", error=str(exc), error_type=type(exc).__name__)

    status = "ok" if db_status == "connected" else "degraded"
    return HealthResponse(
        status=status,
        database=db_status,
        redis=redis_status,
        lakecurrent=lakecurrent_status,
    )
