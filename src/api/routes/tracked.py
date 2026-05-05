"""API routes for domain tracking (add site, list, remove)."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from src.api.middleware.auth import require_org
from src.models.tracked_domain import AddSiteInput, TrackedDomain

router = APIRouter(prefix="/tracked", tags=["tracked"])


@router.post("/add", response_model=TrackedDomain)
async def add_site(request: Request, input_data: AddSiteInput):
    """Add a domain for automated tracking and scheduled scraping."""
    require_org(request)

    from src.db.pool import get_pool
    from src.db.queries.tracked_domains import add_tracked_domain

    pool = await get_pool()
    return await add_tracked_domain(
        pool,
        input_data.domain,
        data_types=input_data.data_types,
        scrape_frequency=input_data.scrape_frequency,
        max_pages=input_data.max_pages,
        webhook_url=input_data.webhook_url,
    )


@router.get("/", response_model=list[TrackedDomain])
async def list_sites(request: Request):
    """List all actively tracked domains."""
    require_org(request)

    from src.db.pool import get_pool
    from src.db.queries.tracked_domains import list_tracked_domains

    pool = await get_pool()
    return await list_tracked_domains(pool)


@router.delete("/{domain}")
async def remove_site(request: Request, domain: str):
    """Remove a domain from tracking (soft delete). Scoped to the caller's org."""
    org_id_str, _, is_admin = require_org(request)

    from src.db.pool import get_pool
    from src.db.queries.tracked_domains import remove_tracked_domain

    pool = await get_pool()
    org_filter = None if is_admin else UUID(org_id_str)
    removed = await remove_tracked_domain(pool, domain, org_id=org_filter)
    if not removed:
        # 404 rather than 403 so cross-org probing can't enumerate domains
        raise HTTPException(status_code=404, detail="Not found")
    return {"success": True}
