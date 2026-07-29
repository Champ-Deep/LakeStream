"""Tech-stack lookup page (paste domains, get detected technologies)."""


import structlog
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.api.routes.web._shared import (
    _require_login,
    get_templates,
)

logger = structlog.get_logger()
router = APIRouter(tags=["web"])


@router.get("/tech", response_class=HTMLResponse)
async def tech_lookup_page(request: Request):
    """Paste-domains technology lookup. Calls /api/tech/bulk from the browser."""
    redirect = _require_login(request)
    if redirect:
        return redirect

    from src.config.settings import get_settings
    from src.scraping.parser.tech_engine import get_catalog

    settings = get_settings()
    try:
        catalog_size = get_catalog().size
    except Exception:
        catalog_size = 0

    return get_templates().TemplateResponse(
        "pages/tech/index.html",
        {
            "request": request,
            "active_page": "tech",
            "catalog_size": catalog_size,
            "catalog_external": bool(settings.tech_catalog_path),
        },
    )
