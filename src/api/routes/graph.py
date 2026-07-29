"""Knowledge-graph API (v2): nodes + edges for a domain's link graph."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from src.api.middleware.auth import get_current_user

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/{domain}")
async def get_domain_graph(domain: str, request: Request, _user=Depends(get_current_user)):
    """Return {nodes, edges, truncated} for a domain, scoped to the user.

    Admins (is_admin) see the graph across all users; regular users see only
    their own crawl edges.
    """
    from src.config.settings import get_settings

    if not get_settings().enable_knowledge_graph:
        return {"nodes": [], "edges": [], "truncated": False, "disabled": True}

    from src.db.pool import get_pool
    from src.services.graph import build_graph

    is_admin = bool(getattr(request.state, "is_admin", False))
    user_id_str = getattr(request.state, "user_id", None)
    user_filter = None if is_admin else (UUID(user_id_str) if user_id_str else None)

    pool = await get_pool()
    return await build_graph(pool, domain, user_id=user_filter)
