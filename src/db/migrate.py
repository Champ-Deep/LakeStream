"""Run SQL migrations from db/migrations/ in sorted order."""

import asyncio
import glob
import os
import sys

import asyncpg
import bcrypt
from dotenv import load_dotenv

# Placeholder stored by migration 012 until a real password is applied.
_PLACEHOLDER_PASSWORD_HASH = "REPLACE_WITH_BCRYPT_HASH"


def get_db_url() -> str:
    """Get database URL with postgres:// to postgresql:// conversion."""
    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://scraper:scraper_dev@localhost:5433/lakeb2b_scraper",
    )
    # Railway/Heroku provide postgres:// but asyncpg requires postgresql://
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    return database_url


async def run_migrations() -> None:
    database_url = get_db_url()
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=3, command_timeout=30)
    assert pool is not None

    await pool.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            name TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    migration_dir = os.path.join(os.path.dirname(__file__), "migrations")
    for path in sorted(glob.glob(f"{migration_dir}/*.sql")):
        name = os.path.basename(path)
        exists = await pool.fetchval("SELECT 1 FROM _migrations WHERE name = $1", name)
        if not exists:
            with open(path) as f:
                sql = f.read()
            await pool.execute(sql)
            await pool.execute("INSERT INTO _migrations (name) VALUES ($1)", name)
            print(f"Applied: {name}")
        else:
            print(f"Skipped (already applied): {name}")

    # Apply ADMIN_PASSWORD when it is set. Never fall back to a built-in password.
    load_dotenv()
    await _ensure_admin_password(pool)

    await pool.close()
    print("Migrations complete.")


def configured_admin_password() -> str:
    """Return ADMIN_PASSWORD, or an empty string when it is missing or blank."""
    return os.environ.get("ADMIN_PASSWORD", "").strip()


def is_unusable_password_hash(password_hash: str | None) -> bool:
    """True when the stored value cannot authenticate a login."""
    if not password_hash or password_hash == _PLACEHOLDER_PASSWORD_HASH:
        return True
    return not (
        password_hash.startswith("$2a$")
        or password_hash.startswith("$2b$")
        or password_hash.startswith("$2y$")
    )


def password_needs_update(password: str, password_hash: str | None) -> bool:
    """True when the stored hash is missing or does not match `password`."""
    if password_hash is None or is_unusable_password_hash(password_hash):
        return True
    try:
        return not bcrypt.checkpw(password.encode(), password_hash.encode())
    except (ValueError, TypeError):
        return True


async def _ensure_admin_password(pool: asyncpg.Pool) -> None:
    """Apply ADMIN_PASSWORD to the seeded admin user.

    The password is read from the environment only. When it is unset and the
    account still has no usable hash, the account is left unable to log in.
    When it is unset and a real hash is already stored, that hash is kept.
    When it is set, the stored hash is updated to match on every run.
    """
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@lakeb2b.internal").strip()
    if not admin_email:
        admin_email = "admin@lakeb2b.internal"
    admin_password = configured_admin_password()

    row = await pool.fetchrow(
        "SELECT id, password_hash, is_admin FROM users WHERE email = $1", admin_email
    )
    if not row:
        print(f"Admin user '{admin_email}' not found in DB — skipping password setup.")
        return

    current_hash = row["password_hash"]

    if not admin_password:
        if is_unusable_password_hash(current_hash):
            print(
                f"ADMIN_PASSWORD is not set. Admin user '{admin_email}' was not "
                "activated and cannot log in. Set ADMIN_PASSWORD and rerun migrations.",
                file=sys.stderr,
            )
        else:
            print(
                f"ADMIN_PASSWORD is not set. Leaving the existing password for "
                f"'{admin_email}' unchanged."
            )
    elif password_needs_update(admin_password, current_hash):
        new_hash = bcrypt.hashpw(admin_password.encode(), bcrypt.gensalt(12)).decode()
        await pool.execute(
            "UPDATE users SET password_hash = $1 WHERE email = $2",
            new_hash,
            admin_email,
        )
        print(f"Admin password updated for {admin_email}.")

    # Ensure admin flag is set
    if not row["is_admin"]:
        await pool.execute("UPDATE users SET is_admin = TRUE WHERE email = $1", admin_email)
        print(f"Admin flag set for {admin_email}.")


if __name__ == "__main__":
    asyncio.run(run_migrations())
