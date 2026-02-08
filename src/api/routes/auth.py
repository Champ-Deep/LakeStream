"""Authentication API routes for signup, login, and user management.

Endpoints:
- POST /auth/signup - Create new organization and user
- POST /auth/login - Authenticate user and return JWT
- GET /auth/me - Get current user profile
"""

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.middleware.auth import get_current_user
from src.db.pool import get_pool
from src.db.queries.users import (
    create_organization,
    create_user,
    get_organization,
    get_user_by_email,
    update_last_login,
)
from src.models.auth import LoginRequest, LoginResponse, SignupRequest, UserProfile
from src.services.auth import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=LoginResponse)
async def signup(request: SignupRequest):
    """Sign up with new organization.

    Creates:
    1. New organization (with generated slug)
    2. First user (assigned role: org_owner)
    3. JWT token for immediate login

    Args:
        request: SignupRequest with email, password, full_name, org_name

    Returns:
        LoginResponse with access token and user profile

    Raises:
        HTTPException 400: If email already exists

    Example:
        POST /api/auth/signup
        {
            "email": "john@acme.com",
            "password": "SecurePass123!",
            "full_name": "John Doe",
            "org_name": "Acme Corp"
        }

        Response:
        {
            "access_token": "eyJhbGc...",
            "token_type": "bearer",
            "user": {
                "id": "123e4567-...",
                "email": "john@acme.com",
                "full_name": "John Doe",
                "org_id": "123e4567-...",
                "org_name": "Acme Corp",
                "role": "org_owner",
                "team_id": null
            }
        }
    """
    pool = await get_pool()

    # Check if email already exists
    existing = await get_user_by_email(pool, request.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )

    # Create organization
    org = await create_organization(pool, request.org_name)

    # Create user (first user is org_owner)
    password_hash = hash_password(request.password)
    user = await create_user(
        pool, email=request.email, password_hash=password_hash, full_name=request.full_name, org_id=org.id, role="org_owner"
    )

    # Update last login
    await update_last_login(pool, user.id)

    # Generate JWT token
    token = create_access_token(user.id, org.id, user.role)

    return LoginResponse(
        access_token=token,
        user=UserProfile(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            org_id=org.id,
            org_name=org.name,
            role=user.role,
            team_id=user.team_id,
        ),
    )


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """Login with email and password.

    Args:
        request: LoginRequest with email and password

    Returns:
        LoginResponse with access token and user profile

    Raises:
        HTTPException 401: If credentials are invalid
        HTTPException 403: If account is disabled

    Example:
        POST /api/auth/login
        {
            "email": "john@acme.com",
            "password": "SecurePass123!"
        }

        Response:
        {
            "access_token": "eyJhbGc...",
            "token_type": "bearer",
            "user": {...}
        }
    """
    pool = await get_pool()

    # Get user by email
    user = await get_user_by_email(pool, request.email)
    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # Check if account is active
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    # Update last login
    await update_last_login(pool, user.id)

    # Generate JWT token
    token = create_access_token(user.id, user.org_id, user.role)

    # Get organization details
    org = await get_organization(pool, user.org_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Organization not found")

    return LoginResponse(
        access_token=token,
        user=UserProfile(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            org_id=user.org_id,
            org_name=org.name,
            role=user.role,
            team_id=user.team_id,
        ),
    )


@router.get("/me", response_model=UserProfile)
async def get_current_user_profile(user=Depends(get_current_user)):
    """Get current user profile from JWT token.

    Requires:
        Authorization: Bearer <token>

    Returns:
        UserProfile with user details

    Raises:
        HTTPException 401: If token is invalid or expired

    Example:
        GET /api/auth/me
        Headers: Authorization: Bearer eyJhbGc...

        Response:
        {
            "id": "123e4567-...",
            "email": "john@acme.com",
            "full_name": "John Doe",
            "org_id": "123e4567-...",
            "org_name": "Acme Corp",
            "role": "org_owner",
            "team_id": null
        }
    """
    pool = await get_pool()

    # Get user details from database
    from src.db.queries.users import get_user_by_id

    db_user = await get_user_by_id(pool, user["user_id"])
    if not db_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Get organization details
    org = await get_organization(pool, db_user.org_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    return UserProfile(
        id=db_user.id,
        email=db_user.email,
        full_name=db_user.full_name,
        org_id=db_user.org_id,
        org_name=org.name,
        role=db_user.role,
        team_id=db_user.team_id,
    )
