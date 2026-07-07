"""Web session auth pages: login, signup, logout.

Password hashing/verification and JWT issuance reuse src/services/auth.py
(shared with the JSON API in src/api/routes/auth.py) rather than
reimplementing them here. The session-cookie vs JWT-cookie dual auth
mechanism itself is intentionally left as-is — see
src/api/middleware/auth.py's TenantContextMiddleware for how both are read.
"""

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.api.routes.web._shared import get_templates

router = APIRouter(tags=["web"])


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=302)
    return get_templates().TemplateResponse(
        "pages/login.html", {"request": request, "error": None, "email": None}
    )


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    """Handle login form submission."""
    from src.db.pool import get_pool
    from src.db.queries.users import get_user_by_email
    from src.services.auth import verify_password

    pool = await get_pool()
    user = await get_user_by_email(pool, email)

    if not user or not verify_password(password, user.password_hash):
        return get_templates().TemplateResponse(
            "pages/login.html",
            {"request": request, "error": "Invalid email or password", "email": email},
            status_code=401,
        )

    if not user.is_active:
        return get_templates().TemplateResponse(
            "pages/login.html",
            {"request": request, "error": "Account is disabled", "email": email},
            status_code=403,
        )

    request.session["user_id"] = str(user.id)
    request.session["org_id"] = str(user.org_id)
    request.session["role"] = user.role
    request.session["email"] = user.email
    request.session["is_admin"] = user.is_admin
    request.session["full_name"] = user.full_name or user.email

    from src.config.settings import get_settings
    from src.services.auth import create_access_token

    cfg = get_settings()
    token = create_access_token(user.id, user.org_id, user.role, is_admin=user.is_admin)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        "access_token",
        token,
        httponly=False,  # must be JS-readable so getAuthHeaders() can build Bearer header
        samesite="lax",
        max_age=cfg.access_token_expire_hours * 3600,
    )
    return response


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    """Signup page."""
    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=302)
    return get_templates().TemplateResponse(
        "pages/signup.html",
        {"request": request, "error": None, "email": None, "full_name": None, "org_name": None},
    )


@router.post("/signup")
async def signup_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    org_name: str = Form(...),
):
    """Handle signup form submission."""
    from src.db.pool import get_pool
    from src.db.queries.users import create_organization, create_user, get_user_by_email
    from src.services.auth import hash_password

    pool = await get_pool()

    # Check if email already exists
    existing = await get_user_by_email(pool, email)
    if existing:
        return get_templates().TemplateResponse(
            "pages/signup.html",
            {
                "request": request,
                "error": "Email already registered",
                "email": email,
                "full_name": full_name,
                "org_name": org_name,
            },
            status_code=400,
        )

    if len(password) < 8:
        return get_templates().TemplateResponse(
            "pages/signup.html",
            {
                "request": request,
                "error": "Password must be at least 8 characters",
                "email": email,
                "full_name": full_name,
                "org_name": org_name,
            },
            status_code=400,
        )

    # Create org + user
    org = await create_organization(pool, org_name)
    password_hash = hash_password(password)
    user = await create_user(
        pool,
        email=email,
        password_hash=password_hash,
        full_name=full_name,
        org_id=org.id,
        role="org_owner",
    )

    # Log them in
    request.session["user_id"] = str(user.id)
    request.session["org_id"] = str(user.org_id)
    request.session["role"] = user.role
    request.session["email"] = user.email
    request.session["is_admin"] = user.is_admin
    request.session["full_name"] = user.full_name or user.email

    from src.config.settings import get_settings
    from src.services.auth import create_access_token

    cfg = get_settings()
    token = create_access_token(user.id, user.org_id, user.role, is_admin=user.is_admin)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        "access_token",
        token,
        httponly=False,
        samesite="lax",
        max_age=cfg.access_token_expire_hours * 3600,
    )
    return response


@router.get("/logout")
async def logout(request: Request):
    """Log out and clear session + JWT cookie."""
    request.session.clear()
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("access_token", path="/")
    return response
