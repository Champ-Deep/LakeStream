"""Authentication service for password hashing and JWT token management.

This module provides:
- bcrypt password hashing (industry standard, adaptive work factor)
- JWT token generation and validation (stateless auth)
- Token claims include user_id, org_id, and role for RLS context
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import bcrypt
import jwt

from src.config.settings import get_settings

settings = get_settings()


def hash_password(password: str) -> str:
    """Hash password using bcrypt with auto-generated salt.

    Args:
        password: Plain text password to hash

    Returns:
        bcrypt hash string (includes salt, cost factor, and hash)

    Example:
        >>> hash_password("SecurePassword123!")
        '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5GyYzS4HullRK'
    """
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode(), salt).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against bcrypt hash.

    Args:
        password: Plain text password to verify
        password_hash: bcrypt hash to compare against

    Returns:
        True if password matches hash, False otherwise

    Example:
        >>> hashed = hash_password("SecurePassword123!")
        >>> verify_password("SecurePassword123!", hashed)
        True
        >>> verify_password("WrongPassword", hashed)
        False
    """
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_access_token(user_id: UUID, org_id: UUID, role: str) -> str:
    """Generate JWT access token with user claims.

    Token contains:
    - user_id: For user identification
    - org_id: For RLS context (set as PostgreSQL session variable)
    - role: For role-based access control (org_owner, team_admin, member)
    - exp: Expiration timestamp (24 hours from now)

    Args:
        user_id: User's UUID
        org_id: Organization's UUID
        role: User's role (org_owner, team_admin, member)

    Returns:
        Signed JWT token string

    Example:
        >>> token = create_access_token(
        ...     user_id=UUID("123e4567-e89b-12d3-a456-426614174000"),
        ...     org_id=UUID("123e4567-e89b-12d3-a456-426614174001"),
        ...     role="member"
        ... )
        >>> # Token format: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
    """
    payload = {
        "user_id": str(user_id),
        "org_id": str(org_id),
        "role": role,
        "exp": datetime.now(UTC) + timedelta(hours=settings.access_token_expire_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Decode and validate JWT access token.

    Args:
        token: JWT token string

    Returns:
        Dictionary containing token claims (user_id, org_id, role, exp)

    Raises:
        jwt.ExpiredSignatureError: If token has expired
        jwt.InvalidTokenError: If token is invalid or signature doesn't match

    Example:
        >>> payload = decode_access_token(token)
        >>> payload["org_id"]
        '123e4567-e89b-12d3-a456-426614174001'
    """
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
