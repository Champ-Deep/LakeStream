"""Settings API routes for org-level configuration (proxy, etc.)."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from src.db.pool import get_pool

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    proxy_url: str = ""


class SettingsResponse(BaseModel):
    proxy_url: str
    proxy_enabled: bool


async def _get_org_id(request: Request, pool) -> str | None:
    """Get org_id from request or fall back to default org."""
    org_id = getattr(request.state, "org_id", None)
    if not org_id:
        org_id = await pool.fetchval("SELECT id FROM organizations WHERE slug = 'default'")
    return org_id


@router.get("/", response_model=SettingsResponse)
async def get_settings(request: Request):
    """Get org settings (proxy configuration)."""
    pool = await get_pool()
    org_id = await _get_org_id(request, pool)

    row = await pool.fetchrow("SELECT proxy_url FROM organizations WHERE id = $1", org_id)
    proxy_url = (row["proxy_url"] or "") if row else ""

    return SettingsResponse(proxy_url=proxy_url, proxy_enabled=bool(proxy_url))


@router.patch("/", response_model=SettingsResponse)
async def update_settings(request: Request, body: SettingsUpdate):
    """Update org settings (proxy URL)."""
    pool = await get_pool()
    org_id = await _get_org_id(request, pool)

    await pool.execute(
        "UPDATE organizations SET proxy_url = $1, updated_at = NOW() WHERE id = $2",
        body.proxy_url,
        org_id,
    )

    return SettingsResponse(proxy_url=body.proxy_url, proxy_enabled=bool(body.proxy_url))
