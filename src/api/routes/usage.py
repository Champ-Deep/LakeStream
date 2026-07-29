"""GET /api/usage — per-user credit/usage summary for the current month."""

from uuid import UUID

from fastapi import APIRouter, Depends

from src.api.middleware.auth import get_current_user
from src.db.pool import get_pool
from src.services.usage import usage_summary

router = APIRouter(prefix="/usage")


@router.get("")
async def get_usage(user: dict = Depends(get_current_user)) -> dict:
    pool = await get_pool()
    summary = await usage_summary(pool, UUID(user["user_id"]))
    return {"success": True, **summary}
