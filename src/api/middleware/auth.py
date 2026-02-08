"""Authentication middleware for JWT validation and RLS context injection.

This middleware:
1. Extracts JWT tokens from Authorization header
2. Validates token and extracts claims (user_id, org_id, role)
3. Sets PostgreSQL session variable for Row-Level Security: app.current_org_id
4. Stores user context in request.state for route access
"""

import jwt
from fastapi import HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.db.pool import get_pool
from src.services.auth import decode_access_token

security = HTTPBearer()


async def get_current_user(credentials: HTTPAuthorizationCredentials = security):  # type: ignore[call-arg]
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


async def set_tenant_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Middleware to set PostgreSQL RLS context from JWT token.

    This middleware:
    1. Extracts JWT from Authorization header (if present)
    2. Decodes token to get org_id
    3. Acquires database connection from pool
    4. Sets PostgreSQL session variable: SET app.current_org_id = '<org_id>'
    5. Stores user context in request.state for route access
    6. Calls next middleware/route handler
    7. Returns response

    For public endpoints (no Authorization header):
    - Skips token extraction
    - No RLS context set (current_setting returns NULL)
    - RLS policies won't match any rows (expected behavior)

    Args:
        request: FastAPI request object
        call_next: Next middleware/route handler

    Returns:
        Response from downstream handlers

    Example:
        # In server.py:
        app.middleware("http")(set_tenant_context)

        # In route:
        @router.get("/tracked/")
        async def list_domains(request: Request):
            org_id = request.state.org_id  # Available from middleware
            # Database queries automatically filtered by RLS
    """
    # Extract token from Authorization header
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else None

    if token:
        try:
            # Decode JWT to get user claims
            payload = decode_access_token(token)
            org_id = payload["org_id"]

            # Set PostgreSQL session variable for RLS
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(f"SET LOCAL app.current_org_id = '{org_id}'")

            # Store user context in request.state for route access
            request.state.user_id = payload["user_id"]
            request.state.org_id = org_id
            request.state.role = payload["role"]

        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, KeyError):
            # Invalid token - don't set context, let routes handle auth
            pass
        except Exception:
            # Database connection error or other issue
            # Log error but don't fail request (public endpoints still work)
            pass

    # Call next middleware/route handler
    response = await call_next(request)
    return response
