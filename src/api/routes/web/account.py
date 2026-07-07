"""Self-service pages: help, settings, and the logged-in user's own account."""

from uuid import UUID

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.api.routes.web._shared import _require_login, get_templates

router = APIRouter(tags=["web"])


# =============================================================================
# HELP PAGES
# =============================================================================


@router.get("/help", response_class=HTMLResponse)
async def help_index(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect
    """Help and documentation page."""
    return get_templates().TemplateResponse(
        "pages/help/index.html", {"request": request, "active_page": "help"}
    )


# =============================================================================
# SETTINGS PAGES
# =============================================================================


@router.get("/settings/api-docs", response_class=HTMLResponse)
async def api_docs_page(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect
    """Full API documentation page."""
    return get_templates().TemplateResponse(
        "pages/settings/api_docs.html",
        {"request": request, "active_page": "settings"},
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect
    """Settings and webhook configuration page."""
    from src.config.settings import get_settings
    from src.db.pool import get_pool

    settings = get_settings()
    webhook_trigger_url = f"{settings.base_url}/api/webhook/trigger"

    # Load org proxy settings for initial render
    proxy_url = ""
    try:
        pool = await get_pool()
        org_id = getattr(request.state, "org_id", None)
        if not org_id:
            org_id = await pool.fetchval("SELECT id FROM organizations WHERE slug = 'default'")
        if org_id:
            proxy_url = (
                await pool.fetchval("SELECT proxy_url FROM organizations WHERE id = $1", org_id)
                or ""
            )
    except Exception:
        pass  # Settings page works without proxy info

    return get_templates().TemplateResponse(
        "pages/settings/index.html",
        {
            "request": request,
            "active_page": "settings",
            "webhook_trigger_url": webhook_trigger_url,
            "proxy_url": proxy_url,
        },
    )


# =============================================================================
# ACCOUNT (self-service)
# =============================================================================


@router.get("/account", response_class=HTMLResponse)
async def account_page(request: Request):
    """Self-service account settings page."""
    redirect = _require_login(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool
    from src.db.queries.users import get_user_by_id

    pool = await get_pool()
    user_id = UUID(request.session["user_id"])
    user = await get_user_by_id(pool, user_id)

    return get_templates().TemplateResponse(
        "pages/account/index.html",
        {
            "request": request,
            "active_page": "account",
            "user": user,
        },
    )


@router.post("/account/update")
async def account_update(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
):
    """Update the logged-in user's name and email."""
    redirect = _require_login(request)
    if redirect:
        return redirect

    from asyncpg import UniqueViolationError

    from src.db.pool import get_pool
    from src.db.queries.users import update_user

    pool = await get_pool()
    user_id = UUID(request.session["user_id"])
    try:
        updated = await update_user(pool, user_id, full_name.strip(), email.strip().lower())
        if updated:
            # Keep session email in sync
            request.session["email"] = updated.email
    except UniqueViolationError:
        return RedirectResponse(url="/account?error=email_exists", status_code=302)
    return RedirectResponse(url="/account?success=updated", status_code=302)
