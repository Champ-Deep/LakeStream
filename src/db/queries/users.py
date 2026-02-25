"""Database queries for user management, organizations, and teams.

These queries use raw SQL via asyncpg for optimal performance.
All queries are async and use asyncpg pool connection management.
"""

from uuid import UUID

from asyncpg import Pool

from src.models.auth import Organization, Team, User


async def create_organization(pool: Pool, name: str) -> Organization:
    """Create a new organization.

    Args:
        pool: asyncpg connection pool
        name: Organization name

    Returns:
        Created Organization object

    Example:
        >>> org = await create_organization(pool, "Acme Corp")
        >>> org.slug
        'acme-corp'
    """
    # Generate URL-friendly slug from name
    slug = name.lower().replace(" ", "-").replace("_", "-")

    # Handle duplicate slugs by appending number
    base_slug = slug
    counter = 1
    while True:
        existing = await pool.fetchrow("SELECT id FROM organizations WHERE slug = $1", slug)
        if not existing:
            break
        slug = f"{base_slug}-{counter}"
        counter += 1

    query = """
        INSERT INTO organizations (name, slug, plan)
        VALUES ($1, $2, 'free')
        RETURNING *
    """
    row = await pool.fetchrow(query, name, slug)
    return Organization(**row)


async def get_organization(pool: Pool, org_id: UUID) -> Organization | None:
    """Get organization by ID.

    Args:
        pool: asyncpg connection pool
        org_id: Organization UUID

    Returns:
        Organization object or None if not found
    """
    query = "SELECT * FROM organizations WHERE id = $1"
    row = await pool.fetchrow(query, org_id)
    return Organization(**row) if row else None


async def create_user(
    pool: Pool,
    email: str,
    password_hash: str,
    full_name: str,
    org_id: UUID,
    role: str,
) -> User:
    """Create a new user.

    Args:
        pool: asyncpg connection pool
        email: User's email (must be unique)
        password_hash: bcrypt password hash
        full_name: User's full name
        org_id: Organization UUID
        role: User role (org_owner, team_admin, member)

    Returns:
        Created User object

    Raises:
        asyncpg.UniqueViolationError: If email already exists

    Example:
        >>> user = await create_user(
        ...     pool,
        ...     "john@acme.com",
        ...     "$2b$12$...",
        ...     "John Doe",
        ...     org_id,
        ...     "member"
        ... )
    """
    query = """
        INSERT INTO users (email, password_hash, full_name, org_id, role)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING *
    """
    row = await pool.fetchrow(query, email, password_hash, full_name, org_id, role)
    return User(**row)


async def get_user_by_email(pool: Pool, email: str) -> User | None:
    """Get user by email address.

    Args:
        pool: asyncpg connection pool
        email: User's email

    Returns:
        User object or None if not found

    Example:
        >>> user = await get_user_by_email(pool, "john@acme.com")
        >>> if user:
        ...     print(user.full_name)
    """
    query = "SELECT * FROM users WHERE email = $1"
    row = await pool.fetchrow(query, email)
    return User(**row) if row else None


async def get_user_by_id(pool: Pool, user_id: UUID) -> User | None:
    """Get user by ID.

    Args:
        pool: asyncpg connection pool
        user_id: User UUID

    Returns:
        User object or None if not found
    """
    query = "SELECT * FROM users WHERE id = $1"
    row = await pool.fetchrow(query, user_id)
    return User(**row) if row else None


async def update_last_login(pool: Pool, user_id: UUID) -> None:
    """Update user's last login timestamp.

    Args:
        pool: asyncpg connection pool
        user_id: User UUID

    Example:
        >>> await update_last_login(pool, user_id)
    """
    query = "UPDATE users SET last_login_at = NOW() WHERE id = $1"
    await pool.execute(query, user_id)


async def list_organization_users(pool: Pool, org_id: UUID) -> list[User]:
    """List all users in an organization.

    Args:
        pool: asyncpg connection pool
        org_id: Organization UUID

    Returns:
        List of User objects

    Example:
        >>> users = await list_organization_users(pool, org_id)
        >>> len(users)
        5
    """
    query = "SELECT * FROM users WHERE org_id = $1 ORDER BY created_at DESC"
    rows = await pool.fetch(query, org_id)
    return [User(**row) for row in rows]


async def create_team(pool: Pool, org_id: UUID, name: str) -> Team:
    """Create a new team within an organization.

    Args:
        pool: asyncpg connection pool
        org_id: Organization UUID
        name: Team name

    Returns:
        Created Team object

    Raises:
        asyncpg.UniqueViolationError: If team name already exists in org

    Example:
        >>> team = await create_team(pool, org_id, "Sales Team")
    """
    query = """
        INSERT INTO teams (org_id, name)
        VALUES ($1, $2)
        RETURNING *
    """
    row = await pool.fetchrow(query, org_id, name)
    return Team(**row)


async def get_team(pool: Pool, team_id: UUID) -> Team | None:
    """Get team by ID.

    Args:
        pool: asyncpg connection pool
        team_id: Team UUID

    Returns:
        Team object or None if not found
    """
    query = "SELECT * FROM teams WHERE id = $1"
    row = await pool.fetchrow(query, team_id)
    return Team(**row) if row else None


async def list_organization_teams(pool: Pool, org_id: UUID) -> list[Team]:
    """List all teams in an organization.

    Args:
        pool: asyncpg connection pool
        org_id: Organization UUID

    Returns:
        List of Team objects

    Example:
        >>> teams = await list_organization_teams(pool, org_id)
    """
    query = "SELECT * FROM teams WHERE org_id = $1 ORDER BY name"
    rows = await pool.fetch(query, org_id)
    return [Team(**row) for row in rows]
