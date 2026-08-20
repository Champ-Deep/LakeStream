"""API routes for domain tracking (add site, list, remove)."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from src.models.tracked_domain import AddSiteInput, TrackedDomain

router = APIRouter(prefix="/tracked", tags=["tracked"])


def _require_auth(request: Request) -> str:
    """Get user_id from request.state (set by TenantContextMiddleware for both JWT and session)."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user_id


def _get_org_filter(request: Request) -> UUID | None:
    """Return org_id for filtering, or None for a super-admin (sees all orgs)."""
    if getattr(request.state, "is_admin", False):
        return None
    org_id_str = getattr(request.state, "org_id", None)
    return UUID(org_id_str) if org_id_str else None


@router.post("/add", response_model=TrackedDomain)
async def add_site(request: Request, input_data: AddSiteInput):
    """Add a domain for automated tracking and scheduled scraping."""
    _require_auth(request)

    from src.db.pool import get_pool
    from src.db.queries.tracked_domains import add_tracked_domain

    org_id_str = getattr(request.state, "org_id", None)
    org_id = UUID(org_id_str) if org_id_str else None

    pool = await get_pool()
    return await add_tracked_domain(
        pool,
        input_data.domain,
        data_types=input_data.data_types,
        scrape_frequency=input_data.scrape_frequency,
        max_pages=input_data.max_pages,
        webhook_url=input_data.webhook_url,
        tech_stack_wappalyzer=input_data.tech_stack_wappalyzer,
        tech_stack_llm_fallback=input_data.tech_stack_llm_fallback,
        org_id=org_id,
    )


@router.get("/", response_model=list[TrackedDomain])
async def list_sites(request: Request):
    """List all actively tracked domains."""
    _require_auth(request)

    from src.db.pool import get_pool
    from src.db.queries.tracked_domains import list_tracked_domains

    pool = await get_pool()
    return await list_tracked_domains(pool, org_id=_get_org_filter(request))


@router.delete("/{domain}")
async def remove_site(request: Request, domain: str):
    """Remove a domain from tracking (soft delete)."""
    _require_auth(request)

    from src.db.pool import get_pool
    from src.db.queries.tracked_domains import remove_tracked_domain

    pool = await get_pool()
    removed = await remove_tracked_domain(pool, domain, org_id=_get_org_filter(request))
    if not removed:
        raise HTTPException(status_code=404, detail="Tracked domain not found")
    return {"success": True}
