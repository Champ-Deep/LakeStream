"""Clerk session-JWT verification (v2).

Verifies Clerk-issued session tokens against Clerk's JWKS (RS256) and maps the
claims to LakeStream's auth context. Only used when auth_provider="clerk".
"""

import jwt
import structlog
from jwt import PyJWKClient

from src.config.settings import get_settings

log = structlog.get_logger()

_jwks_client: PyJWKClient | None = None


class ClerkAuthError(Exception):
    """Raised when a Clerk token is missing, malformed, or fails verification."""


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        url = get_settings().clerk_jwks_url
        if not url:
            raise ClerkAuthError("CLERK_JWKS_URL is not configured")
        # PyJWKClient caches fetched keys internally.
        _jwks_client = PyJWKClient(url, cache_keys=True)
    return _jwks_client


def verify_clerk_token(token: str) -> dict:
    """Verify a Clerk session JWT and return its claims.

    Checks signature (RS256 via JWKS), expiry, and issuer when configured.
    Raises ClerkAuthError on any failure.
    """
    if not token:
        raise ClerkAuthError("missing token")
    settings = get_settings()
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        options = {"require": ["exp"]}
        kwargs: dict = {"algorithms": ["RS256"], "options": options}
        if settings.clerk_issuer:
            kwargs["issuer"] = settings.clerk_issuer
        claims = jwt.decode(token, signing_key.key, **kwargs)
    except ClerkAuthError:
        raise
    except Exception as e:  # jwt.PyJWTError and network/JWKS errors
        raise ClerkAuthError(str(e)) from e
    return claims


def claims_to_context(claims: dict) -> dict:
    """Map Clerk claims to LakeStream's {clerk_user_id, email, is_admin, role}.

    Super-admin is signalled by publicMetadata.role == "super_admin". Clerk
    templates may surface public metadata under different claim names, so a few
    common shapes are checked.
    """
    clerk_user_id = claims.get("sub", "")
    email = (
        claims.get("email")
        or claims.get("email_address")
        or _first_email(claims)
        or ""
    )
    public_meta = (
        claims.get("public_metadata")
        or claims.get("publicMetadata")
        or claims.get("metadata")
        or {}
    )
    role = (public_meta or {}).get("role", "member")
    is_admin = role == "super_admin"
    return {
        "clerk_user_id": clerk_user_id,
        "email": email,
        "role": role,
        "is_admin": is_admin,
    }


def _first_email(claims: dict) -> str | None:
    emails = claims.get("email_addresses") or claims.get("emails")
    if isinstance(emails, list) and emails:
        first = emails[0]
        if isinstance(first, dict):
            return first.get("email_address") or first.get("email")
        return first
    return None
