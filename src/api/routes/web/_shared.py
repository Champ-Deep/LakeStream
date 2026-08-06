"""Helpers shared across the web UI route modules.

These overlap with the session-authorization work already done by
TenantContextMiddleware (src/api/middleware/auth.py), which redirects
unauthenticated requests before they reach a handler. They are kept because
handlers still rely on `_get_user_filter` to scope queries to a non-admin
user's own rows, and on `_require_admin` to gate admin-only actions.

The `_auth_*` helpers read request.state rather than the session directly, so
Clerk, legacy JWT and cookie-session logins all resolve the same way.
"""

from uuid import UUID

from fastapi import Request
from fastapi.responses import RedirectResponse


def get_templates():
    """Get templates instance from app state."""
    from src.server import templates

    return templates


def _auth_user_id(request: Request):
    """Authenticated user id from request.state (set by the auth middleware for
    Clerk, legacy JWT, and session alike)."""
    return getattr(request.state, "user_id", None)


def _auth_is_admin(request: Request) -> bool:
    """Super-admin flag from request.state (Clerk publicMetadata.role, legacy is_admin)."""
    return bool(getattr(request.state, "is_admin", False))


def _auth_org_id(request: Request):
    """Authenticated org id from request.state (provider-agnostic)."""
    return getattr(request.state, "org_id", None)


def _require_login(request: Request):
    """Return a redirect to /login if the user is not authenticated, else None."""
    if not _auth_user_id(request):
        return RedirectResponse(url="/login", status_code=302)
    return None


def _require_admin(request: Request):
    """Return a redirect if the user is not a super-admin, else None."""
    redir = _require_login(request)
    if redir:
        return redir
    if not _auth_is_admin(request):
        return RedirectResponse(url="/", status_code=302)
    return None


def _get_user_filter(request: Request) -> UUID | None:
    """Return user_id for filtering data, or None for super-admin (sees all)."""
    if _auth_is_admin(request):
        return None  # Super-admin sees everything (god view)
    uid = _auth_user_id(request)
    return UUID(uid) if uid else None


def _clerk_frontend_api(publishable_key: str) -> str:
    """Derive the Clerk Frontend API host encoded in a publishable key.

    Keys look like ``pk_test_<base64(host$)>``; decoding yields e.g.
    ``clerk.example.com$``. Returns "" if the key is absent/malformed.
    """
    if not publishable_key:
        return ""
    try:
        import base64

        encoded = publishable_key.split("_", 2)[-1]
        decoded = base64.b64decode(encoded + "===").decode("utf-8")
        return decoded.rstrip("$")
    except Exception:
        return ""
