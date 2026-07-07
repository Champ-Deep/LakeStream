"""Admin-only user management CRUD pages."""

from uuid import UUID

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.api.routes.web._shared import _require_admin, get_templates

router = APIRouter(tags=["web"])


@router.get("/users", response_class=HTMLResponse)
async def users_list(request: Request):
    """User management page (admin only)."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool
    from src.db.queries.users import list_users_with_counts

    pool = await get_pool()

    # Get all users with their job counts
    users = await list_users_with_counts(pool)

    return get_templates().TemplateResponse(
        "pages/users/list.html",
        {
            "request": request,
            "active_page": "users",
            "users": users,
        },
    )


@router.post("/users/create")
async def create_user_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    role: str = Form(default="member"),
    is_admin: bool = Form(default=False),
):
    """Create a new user (admin only)."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool
    from src.db.queries.users import create_user, get_user_by_email, set_user_admin
    from src.services.auth import hash_password

    pool = await get_pool()

    # Check if email already exists
    existing = await get_user_by_email(pool, email)
    if existing:
        # Re-render with error
        return RedirectResponse(url="/users?error=email_exists", status_code=302)

    # Use the admin's org_id
    org_id = UUID(request.session["org_id"])

    password_hash = hash_password(password)
    await create_user(
        pool,
        email=email,
        password_hash=password_hash,
        full_name=full_name,
        org_id=org_id,
        role=role,
    )

    # Set is_admin flag if needed
    if is_admin:
        user = await get_user_by_email(pool, email)
        if user:
            await set_user_admin(pool, user.id, True)

    return RedirectResponse(url="/users?success=created", status_code=302)


@router.post("/users/{user_id}/toggle-active")
async def toggle_user_active_route(request: Request, user_id: UUID):
    """Enable/disable a user (admin only)."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool
    from src.db.queries.users import toggle_user_active

    pool = await get_pool()

    # Don't allow disabling yourself
    if str(user_id) == request.session.get("user_id"):
        return RedirectResponse(url="/users?error=cannot_disable_self", status_code=302)

    await toggle_user_active(pool, user_id)
    return RedirectResponse(url="/users", status_code=302)


@router.post("/users/{user_id}/toggle-admin")
async def toggle_user_admin_route(request: Request, user_id: UUID):
    """Toggle admin status for a user (admin only)."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool
    from src.db.queries.users import toggle_user_admin

    pool = await get_pool()

    # Don't allow removing your own admin
    if str(user_id) == request.session.get("user_id"):
        return RedirectResponse(url="/users?error=cannot_change_self", status_code=302)

    await toggle_user_admin(pool, user_id)
    return RedirectResponse(url="/users", status_code=302)


@router.post("/users/{user_id}/delete")
async def delete_user_route(request: Request, user_id: UUID):
    """Delete a user (admin only)."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool
    from src.db.queries.users import delete_user

    pool = await get_pool()

    # Don't allow deleting yourself
    if str(user_id) == request.session.get("user_id"):
        return RedirectResponse(url="/users?error=cannot_delete_self", status_code=302)

    await delete_user(pool, user_id)
    return RedirectResponse(url="/users", status_code=302)


@router.post("/users/{user_id}/edit")
async def edit_user(
    request: Request,
    user_id: UUID,
    full_name: str = Form(...),
    email: str = Form(...),
):
    """Edit a user's name and email (admin only)."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    from asyncpg import UniqueViolationError

    from src.db.pool import get_pool
    from src.db.queries.users import update_user

    pool = await get_pool()
    try:
        await update_user(pool, user_id, full_name.strip(), email.strip().lower())
    except UniqueViolationError:
        return RedirectResponse(url="/users?error=email_exists", status_code=302)
    return RedirectResponse(url="/users?success=updated", status_code=302)
