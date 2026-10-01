"""Tech Detect page (single-URL lookup, CSV batch, category filtering).

Ported from the monolithic src/api/routes/web.py on tech-detect-new into this
package, so both tech surfaces coexist: this Wappalyzer-backed /tech-detect and
the catalog-backed /tech in web/tech.py.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.api.routes.web._shared import (
    _require_login,
    get_templates,
)

router = APIRouter(tags=["web"])


@router.get("/tech-detect", response_class=HTMLResponse)
async def tech_detect_page(request: Request, run: str | None = None):
    """Tech Detect: single-URL lookup, CSV batch, and category filtering."""
    redirect = _require_login(request)
    if redirect:
        return redirect

    from src.services.tech_categories import CANONICAL_CATEGORIES

    return get_templates().TemplateResponse(
        "pages/tech_detect/index.html",
        {
            "request": request,
            "active_page": "tech_detect",
            "categories": CANONICAL_CATEGORIES,
            "resume_run": run,
        },
    )
