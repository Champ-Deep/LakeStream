"""Authentication models for multi-tenant authentication system.

These models define the request/response structure for JWT-based authentication.
Used by FastAPI routes for validation and serialization.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Request body for user login."""

    email: EmailStr
    password: str = Field(..., min_length=8)


class SignupRequest(BaseModel):
    """Request body for new user signup (creates new organization)."""

    email: EmailStr
    password: str = Field(..., min_length=8, description="Minimum 8 characters")
    full_name: str = Field(..., min_length=1)
    org_name: str = Field(..., min_length=1, description="Organization name (new org will be created)")


class InviteUserRequest(BaseModel):
    """Request body for inviting a user to existing organization."""

    email: EmailStr
    role: str = Field(default="member", pattern="^(org_owner|team_admin|member)$")
    team_id: UUID | None = None


class UserProfile(BaseModel):
    """User profile information included in auth responses."""

    id: UUID
    email: str
    full_name: str | None
    org_id: UUID
    org_name: str
    role: str  # org_owner, team_admin, member
    team_id: UUID | None


class LoginResponse(BaseModel):
    """Response body for successful authentication."""

    access_token: str
    token_type: str = "bearer"
    user: UserProfile


# Database models (match PostgreSQL schema)


class Organization(BaseModel):
    """Organization model (top-level tenant)."""

    id: UUID
    name: str
    slug: str
    plan: str  # free, pro, enterprise
    max_users: int
    max_domains: int
    max_signals_per_month: int
    billing_email: str | None
    created_at: datetime
    updated_at: datetime


class Team(BaseModel):
    """Team model (optional grouping within organization)."""

    id: UUID
    org_id: UUID
    name: str
    created_at: datetime


class User(BaseModel):
    """User model."""

    id: UUID
    org_id: UUID
    team_id: UUID | None
    email: str
    password_hash: str
    full_name: str | None
    role: str  # org_owner, team_admin, member
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApiKey(BaseModel):
    """API key model for programmatic access."""

    id: UUID
    user_id: UUID
    org_id: UUID
    key_hash: str  # SHA256 hash
    name: str
    last_used_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
