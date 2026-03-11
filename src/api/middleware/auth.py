"""Authentication middleware for JWT validation and RLS context injection.

This middleware:
1. Extracts JWT tokens from Authorization header
2. Validates token and extracts claims (user_id, org_id, role)
3. Sets PostgreSQL session variable for Row-Level Security: app.current_org_id
4. Stores user context in request.state for route access
"""

import jwt
import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware

from src.db.pool import get_pool
from src.services.auth import decode_access_token

security = HTTPBearer()
log = structlog.get_logger()


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Extract and validate user from JWT token.

    This dependency can be used in route handlers to require authentication:

    @router.get("/protected")
    async def protected_route(user = Depends(get_current_user)):
        return {"user_id": user["user_id"], "org_id": user["org_id"]}

    Args:
        credentials: HTTP Authorization header with Bearer token

    Returns:
        Dictionary with user claims: {user_id, org_id, role, exp}

    Raises:
        HTTPException 401: If token is expired or invalid
    """
    try:
        payload = decode_access_token(credentials.credentials)
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


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Middleware to set request.state from JWT or session.

    Registered AFTER SessionMiddleware so request.session is available.
    """

    async def dispatch(self, request: Request, call_next):
        # Extract token from Authorization header
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else None

        if token:
            try:
                payload = decode_access_token(token)
                org_id = payload["org_id"]

                pool = await get_pool()
                async with pool.acquire() as conn:
                    await conn.execute("SELECT set_config('app.current_org_id', $1, true)", org_id)

                request.state.user_id = payload["user_id"]
                request.state.org_id = org_id
                request.state.role = payload["role"]
                request.state.is_admin = payload.get("is_admin", False)

            except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, KeyError):
                pass
            except Exception as exc:
                log.warning(
                    "tenant_context_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
        else:
            # Fall back to session-based auth (web UI + API calls from browser)
            try:
                session = request.session
                if session and session.get("user_id"):
                    request.state.user_id = session["user_id"]
                    request.state.org_id = session.get("org_id")
                    request.state.role = session.get("role")
                    request.state.is_admin = session.get("is_admin", False)
            except (AssertionError, AttributeError):
                pass

        response = await call_next(request)
        return response
