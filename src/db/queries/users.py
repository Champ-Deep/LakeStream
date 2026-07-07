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


async def update_user(
    pool: Pool,
    user_id: UUID,
    full_name: str,
    email: str,
) -> User | None:
    """Update a user's full name and email.

    Returns:
        Updated User object, or None if user not found.

    Raises:
        asyncpg.UniqueViolationError: If the new email is already taken.
    """
    query = """
        UPDATE users
        SET full_name = $1, email = $2, updated_at = NOW()
        WHERE id = $3
        RETURNING *
    """
    row = await pool.fetchrow(query, full_name, email, user_id)
    return User(**row) if row else None


async def list_users_with_counts(pool: Pool) -> list[dict]:
    """List all users with org name, job count, and scraped-data count (admin dashboard).

    Returns a list of plain dicts (rather than the User model) since this view
    joins in aggregate counts that aren't part of the users table itself.
    """
    rows = await pool.fetch(
        """
        SELECT u.*,
               o.name as org_name,
               COALESCE(j.job_count, 0) as job_count,
               COALESCE(d.data_count, 0) as data_count
        FROM users u
        JOIN organizations o ON u.org_id = o.id
        LEFT JOIN (
            SELECT user_id, COUNT(*) as job_count FROM scrape_jobs GROUP BY user_id
        ) j ON j.user_id = u.id
        LEFT JOIN (
            SELECT user_id, COUNT(*) as data_count FROM scraped_data GROUP BY user_id
        ) d ON d.user_id = u.id
        ORDER BY u.created_at DESC
        """
    )
    return [
        {
            "id": row["id"],
            "email": row["email"],
            "full_name": row["full_name"],
            "role": row["role"],
            "is_admin": row["is_admin"],
            "is_active": row["is_active"],
            "org_name": row["org_name"],
            "job_count": row["job_count"],
            "data_count": row["data_count"],
            "last_login_at": row["last_login_at"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


async def set_user_admin(pool: Pool, user_id: UUID, is_admin: bool) -> None:
    """Set a user's is_admin flag explicitly (used after admin-created signup)."""
    await pool.execute("UPDATE users SET is_admin = $2 WHERE id = $1", user_id, is_admin)


async def toggle_user_active(pool: Pool, user_id: UUID) -> None:
    """Flip a user's is_active flag (admin enable/disable action)."""
    await pool.execute(
        "UPDATE users SET is_active = NOT is_active, updated_at = NOW() WHERE id = $1",
        user_id,
    )


async def toggle_user_admin(pool: Pool, user_id: UUID) -> None:
    """Flip a user's is_admin flag (admin action)."""
    await pool.execute(
        "UPDATE users SET is_admin = NOT is_admin, updated_at = NOW() WHERE id = $1",
        user_id,
    )


async def delete_user(pool: Pool, user_id: UUID) -> None:
    """Delete a user (admin action)."""
    await pool.execute("DELETE FROM users WHERE id = $1", user_id)


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
