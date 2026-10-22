"""Inbound API protection (v2.1): rate limiting + usage metering.

Two middlewares with different placement:

- RateLimitMiddleware — OUTER (cheap): Redis fixed-window per minute keyed by
  API key (hashed) or client IP. 429 + Retry-After before any auth/DB work.
  Fails open if Redis is unreachable.
- UsageMeteringMiddleware — INNER (after auth): optional credit enforcement
  (402 when over the monthly allowance) and fire-and-forget usage recording
  for successful billable POSTs.
"""

import hashlib
import time
from uuid import UUID

import structlog
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.config.settings import get_settings

log = structlog.get_logger()

_EXEMPT_PATHS = ("/api/health", "/ping", "/api/auth/login")

_redis = None


async def _get_redis():
    global _redis
    if _redis is None:
        import redis.asyncio as aioredis

        settings = get_settings()
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _client_key(request) -> str:
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return "key:" + hashlib.sha256(api_key.encode()).hexdigest()[:16]
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else "unknown"
    )
    return f"ip:{ip}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        settings = get_settings()
        path = request.url.path
        if (
            not settings.rate_limit_enabled
            or not path.startswith("/api/")
            or path.startswith(_EXEMPT_PATHS)
        ):
            return await call_next(request)

        window = int(time.time() // 60)
        key = f"ls:ratelimit:{_client_key(request)}:{window}"
        try:
            r = await _get_redis()
            count = await r.incr(key)
            if count == 1:
                await r.expire(key, 90)
        except Exception as e:
            log.warning("rate_limit_redis_unavailable", error=str(e))
            return await call_next(request)

        limit = settings.rate_limit_per_minute
        if count > limit:
            retry_after = 60 - int(time.time() % 60)
            return JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "error": f"Rate limit exceeded ({limit} requests/minute)",
                },
                headers={"Retry-After": str(retry_after)},
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - count))
        return response


class UsageMeteringMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        from src.services.usage import (
            BILLABLE_PREFIXES,
            over_credit_limit,
            record_usage,
        )

        path = request.url.path
        billable = request.method == "POST" and path.startswith(BILLABLE_PREFIXES)
        if not billable:
            return await call_next(request)

        user_id_raw = getattr(request.state, "user_id", None)
        user_id = None
        if user_id_raw:
            try:
                user_id = UUID(str(user_id_raw))
            except ValueError:
                pass

        pool = None
        if user_id:
            try:
                from src.db.pool import get_pool

                pool = await get_pool()
                if await over_credit_limit(pool, user_id):
                    settings = get_settings()
                    return JSONResponse(
                        status_code=402,
                        content={
                            "success": False,
                            "error": (
                                f"Monthly credit limit reached "
                                f"({settings.free_monthly_credits} credits)"
                            ),
                        },
                    )
            except Exception as e:
                log.warning("credit_check_failed", error=str(e))

        response = await call_next(request)

        if pool is not None and response.status_code < 400:
            org_raw = getattr(request.state, "org_id", None)
            org_id = None
            if org_raw:
                try:
                    org_id = UUID(str(org_raw))
                except ValueError:
                    pass
            await record_usage(
                pool, endpoint=path, user_id=user_id, org_id=org_id
            )
        return response
