"""API routes for domain tracking (add site, list, remove)."""

from fastapi import APIRouter, Depends

from src.api.middleware.auth import get_current_user
from src.models.tracked_domain import AddSiteInput, TrackedDomain

router = APIRouter(prefix="/tracked", tags=["tracked"])


@router.post("/add", response_model=TrackedDomain)
async def add_site(input_data: AddSiteInput, user: dict = Depends(get_current_user)):
    """Add a domain for automated tracking and scheduled scraping."""
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
async def list_sites(user: dict = Depends(get_current_user)):
    """List all actively tracked domains."""
    from src.db.pool import get_pool
    from src.db.queries.tracked_domains import list_tracked_domains

    pool = await get_pool()
    return await list_tracked_domains(pool)


@router.delete("/{domain}")
async def remove_site(domain: str, user: dict = Depends(get_current_user)):
    """Remove a domain from tracking (soft delete)."""
    from src.db.pool import get_pool
    from src.db.queries.tracked_domains import remove_tracked_domain

    pool = await get_pool()
    await remove_tracked_domain(pool, domain)
    return {"success": True}
