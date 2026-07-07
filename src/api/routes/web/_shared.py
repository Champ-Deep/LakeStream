"""Helpers shared across the web UI route modules.

These duplicate some of the session-authorization concerns already handled
by TenantContextMiddleware (src/api/middleware/auth.py), which redirects
unauthenticated requests before they ever reach a route handler. They are
kept here (rather than removed) because individual handlers still rely on
`_get_user_filter` to scope queries to a non-admin user's own data, and on
`_require_admin` to gate admin-only actions beyond plain authentication.
"""

from uuid import UUID

from fastapi import Request
from fastapi.responses import RedirectResponse


def get_templates():
    """Get templates instance from app state."""
    from src.server import templates

    return templates


def _require_login(request: Request):
    """Return a redirect to /login if the user is not in session, else None."""
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return None


def _require_admin(request: Request):
    """Return a redirect if user is not admin, else None."""
    redir = _require_login(request)
    if redir:
        return redir
    if not request.session.get("is_admin"):
        return RedirectResponse(url="/", status_code=302)
    return None


def _get_user_filter(request: Request) -> UUID | None:
    """Return user_id for filtering data, or None if admin (sees all)."""
    if request.session.get("is_admin"):
        return None  # Admin sees everything
    uid = request.session.get("user_id")
    return UUID(uid) if uid else None
