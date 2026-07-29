"""Admin user-management routes."""

from uuid import UUID

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.api.routes.web._shared import (
    _auth_org_id,
    _auth_user_id,
    _require_admin,
    get_templates,
)

logger = structlog.get_logger()
router = APIRouter(tags=["web"])


@router.get("/users", response_class=HTMLResponse)
async def users_list(request: Request):
    """User management page (admin only)."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool

    pool = await get_pool()

    # Get all users with their job counts
    rows = await pool.fetch(
        """
        SELECT u.*,
               o.name as org_name,
               COALESCE(j.job_count, 0) as job_count,
               COALESCE(d.data_count, 0) as data_count
        FROM users u
        JOIN organizations o ON u.org_id = o.id
        LEFT JOIN (
            SELECT user_id, COUNT(*) as job_count FROM scrape_jobs GROUP BY user_id
        ) j ON j.user_id = u.id
        LEFT JOIN (
            SELECT user_id, COUNT(*) as data_count FROM scraped_data GROUP BY user_id
        ) d ON d.user_id = u.id
        ORDER BY u.created_at DESC
        """
    )

    users = []
    for row in rows:
        users.append(
            {
                "id": row["id"],
                "email": row["email"],
                "full_name": row["full_name"],
                "role": row["role"],
                "is_admin": row["is_admin"],
                "is_active": row["is_active"],
                "org_name": row["org_name"],
                "job_count": row["job_count"],
                "data_count": row["data_count"],
                "last_login_at": row["last_login_at"],
                "created_at": row["created_at"],
            }
        )

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
    from src.db.queries.users import create_user, get_user_by_email
    from src.services.auth import hash_password

    pool = await get_pool()

    # Check if email already exists
    existing = await get_user_by_email(pool, email)
    if existing:
        # Re-render with error
        return RedirectResponse(url="/users?error=email_exists", status_code=302)

    # Use the admin's org_id
    org_id = UUID(_auth_org_id(request))

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
            await pool.execute("UPDATE users SET is_admin = TRUE WHERE id = $1", user.id)

    return RedirectResponse(url="/users?success=created", status_code=302)


@router.post("/users/{user_id}/toggle-active")
async def toggle_user_active(request: Request, user_id: UUID):
    """Enable/disable a user (admin only)."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool

    pool = await get_pool()

    # Don't allow disabling yourself
    if str(user_id) == _auth_user_id(request):
        return RedirectResponse(url="/users?error=cannot_disable_self", status_code=302)

    await pool.execute(
        "UPDATE users SET is_active = NOT is_active, updated_at = NOW() WHERE id = $1",
        user_id,
    )
    return RedirectResponse(url="/users", status_code=302)


@router.post("/users/{user_id}/toggle-admin")
async def toggle_user_admin(request: Request, user_id: UUID):
    """Toggle admin status for a user (admin only)."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool

    pool = await get_pool()

    # Don't allow removing your own admin
    if str(user_id) == _auth_user_id(request):
        return RedirectResponse(url="/users?error=cannot_change_self", status_code=302)

    await pool.execute(
        "UPDATE users SET is_admin = NOT is_admin, updated_at = NOW() WHERE id = $1",
        user_id,
    )
    return RedirectResponse(url="/users", status_code=302)


@router.post("/users/{user_id}/delete")
async def delete_user(request: Request, user_id: UUID):
    """Delete a user (admin only)."""
    redirect = _require_admin(request)
    if redirect:
        return redirect

    from src.db.pool import get_pool

    pool = await get_pool()

    # Don't allow deleting yourself
    if str(user_id) == _auth_user_id(request):
        return RedirectResponse(url="/users?error=cannot_delete_self", status_code=302)

    await pool.execute("DELETE FROM users WHERE id = $1", user_id)
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
