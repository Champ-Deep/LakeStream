"""Settings API routes for org-level configuration (proxy, webhooks)."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from src.db.pool import get_pool

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    proxy_url: str | None = None
    webhook_url: str | None = None
    webhook_auto_send: bool | None = None
    webhook_include_metadata: bool | None = None


class SettingsResponse(BaseModel):
    proxy_url: str
    proxy_enabled: bool
    webhook_url: str
    webhook_auto_send: bool
    webhook_include_metadata: bool


async def _get_org_id(request: Request, pool) -> str | None:
    """Get org_id from request or fall back to default org."""
    org_id = getattr(request.state, "org_id", None)
    if not org_id:
        org_id = await pool.fetchval("SELECT id FROM organizations WHERE slug = 'default'")
    return org_id


@router.get("/", response_model=SettingsResponse)
async def get_settings(request: Request):
    """Get org settings (proxy + webhook configuration)."""
    pool = await get_pool()
    org_id = await _get_org_id(request, pool)

    row = await pool.fetchrow(
        "SELECT proxy_url, webhook_url, webhook_auto_send, webhook_include_metadata "
        "FROM organizations WHERE id = $1",
        org_id,
    )
    if not row:
        return SettingsResponse(
            proxy_url="", proxy_enabled=False,
            webhook_url="", webhook_auto_send=False, webhook_include_metadata=False,
        )

    proxy_url = row["proxy_url"] or ""
    return SettingsResponse(
        proxy_url=proxy_url,
        proxy_enabled=bool(proxy_url),
        webhook_url=row["webhook_url"] or "",
        webhook_auto_send=row["webhook_auto_send"] or False,
        webhook_include_metadata=row["webhook_include_metadata"] or False,
    )


@router.patch("/", response_model=SettingsResponse)
async def update_settings(request: Request, body: SettingsUpdate):
    """Update org settings (proxy URL, webhook config)."""
    pool = await get_pool()
    org_id = await _get_org_id(request, pool)

    # Build dynamic SET clause for only provided fields
    sets = ["updated_at = NOW()"]
    vals: list[object] = []
    idx = 1

    for field, value in [
        ("proxy_url", body.proxy_url),
        ("webhook_url", body.webhook_url),
        ("webhook_auto_send", body.webhook_auto_send),
        ("webhook_include_metadata", body.webhook_include_metadata),
    ]:
        if value is not None:
            sets.append(f"{field} = ${idx}")
            vals.append(value)
            idx += 1

    vals.append(org_id)
    query = f"UPDATE organizations SET {', '.join(sets)} WHERE id = ${idx}"
    await pool.execute(query, *vals)

    # Return current state
    return await get_settings(request)
