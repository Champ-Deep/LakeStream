"""Authentication middleware for JWT validation and RLS context injection.

This middleware:
1. Extracts JWT tokens from Authorization header OR access_token cookie
2. Validates token and extracts claims (user_id, org_id, role)
3. Sets PostgreSQL session variable for Row-Level Security: app.current_org_id
4. Stores user context in request.state for route access
5. Redirects unauthenticated users to /login for protected web routes
"""

import asyncio
import hashlib

import jwt
import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware

from src.db.pool import get_pool
from src.services.auth import decode_access_token

security = HTTPBearer()
log = structlog.get_logger()

# Web routes that don't require authentication
PUBLIC_PATHS = {"/login", "/ping"}
PUBLIC_PREFIXES = ("/api/", "/static/")


async def get_current_user(request: Request):
    """Extract and validate user from JWT token, API key, or session cookie.

    Checks authentication sources in order:
    1. request.state (set by TenantContextMiddleware from API key / JWT / session)
    2. Authorization: Bearer <JWT> header (fallback for direct use)

    This dependency can be used in route handlers to require authentication:

    @router.get("/protected")
    async def protected_route(user = Depends(get_current_user)):
        return {"user_id": user["user_id"], "org_id": user["org_id"]}

    Returns:
        Dictionary with user claims: {user_id, org_id, role, is_admin}

    Raises:
        HTTPException 401: If no valid authentication found
    """
    # 1. Check if middleware already authenticated (API key, cookie session, or JWT)
    user_id = getattr(request.state, "user_id", None)
    org_id = getattr(request.state, "org_id", None)
    if user_id and org_id:
        return {
            "user_id": str(user_id),
            "org_id": str(org_id),
            "role": getattr(request.state, "role", "member"),
            "is_admin": getattr(request.state, "is_admin", False),
        }

    # 2. Fallback: direct JWT bearer token check (for endpoints bypassing middleware)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            payload = decode_access_token(auth_header.replace("Bearer ", ""))
            return payload
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except jwt.InvalidTokenError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Middleware to set request.state from JWT or session.

    Registered AFTER SessionMiddleware so request.session is available.
    Extracts JWT from Authorization header or access_token cookie,
    sets PostgreSQL RLS context, and redirects unauthenticated users.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 1. Check for API key auth (X-API-Key header) — used by Chrome extension
        api_key_header = request.headers.get("X-API-Key")
        authenticated = False

        if api_key_header:
            try:
                from src.db.queries.api_keys import get_api_key_by_hash, touch_api_key

                key_hash = hashlib.sha256(api_key_header.encode()).hexdigest()
                pool = await get_pool()
                key_record = await get_api_key_by_hash(pool, key_hash)
                if key_record:
                    request.state.user_id = str(key_record["user_id"])
                    request.state.org_id = str(key_record["org_id"])
                    request.state.role = "member"
                    request.state.is_admin = False
                    authenticated = True
                    asyncio.create_task(touch_api_key(pool, key_record["id"]))
            except Exception as exc:
                log.warning("api_key_auth_error", error=str(exc))

        # 2. Extract token from Authorization header OR access_token cookie
        token = None
        if not authenticated:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header.replace("Bearer ", "")
            elif "access_token" in request.cookies:
                token = request.cookies["access_token"]

        if not authenticated and token:
            try:
                payload = decode_access_token(token)
                org_id = payload["org_id"]

                # Set PostgreSQL RLS context
                pool = await get_pool()
                async with pool.acquire() as conn:
                    await conn.execute("SELECT set_config('app.current_org_id', $1, true)", org_id)

                request.state.user_id = payload["user_id"]
                request.state.org_id = org_id
                request.state.role = payload["role"]
                request.state.is_admin = payload.get("is_admin", False)
                authenticated = True

            except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, KeyError):
                # Invalid/expired token — clear the bad cookie and redirect (web routes only)
                is_web = not any(path.startswith(p) for p in PUBLIC_PREFIXES)
                if "access_token" in request.cookies and is_web:
                    response = RedirectResponse(url="/login", status_code=302)
                    response.delete_cookie("access_token", path="/")
                    return response
            except Exception as exc:
                log.warning(
                    "tenant_context_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
        else:
            # Fall back to session-based auth (web UI + server-side login)
            try:
                session = request.session
                if session and session.get("user_id"):
                    request.state.user_id = session["user_id"]
                    request.state.org_id = session.get("org_id")
                    request.state.role = session.get("role")
                    request.state.is_admin = session.get("is_admin", False)
                    authenticated = True
            except (AssertionError, AttributeError):
                pass

        # Redirect authenticated users away from login/signup to dashboard
        if authenticated and path in {"/login", "/signup"}:
            return RedirectResponse(url="/", status_code=302)

        # Protect web routes — redirect unauthenticated users to /login
        is_public = path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES)
        if not is_public and not authenticated:
            return RedirectResponse(url="/login", status_code=302)

        response = await call_next(request)
        return response
