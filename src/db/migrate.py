"""Run SQL migrations from db/migrations/ in sorted order."""

import asyncio
import glob
import os

import asyncpg
import bcrypt


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


# Mapping from old (deprecated) migration filenames to their new canonical
# names. When _migrations contains an old-name row, we copy the bookkeeping
# under the new name so the renamed file isn't re-applied on existing DBs.
# Safe to keep around indefinitely — it's a no-op when the DB has no old rows.
_RENAMED_MIGRATIONS = {
    # Was a duplicate "016" alongside 016_add_user_id_and_admin.sql; renamed
    # to 024 to keep numeric ordering deterministic. See plan.md S1.5.
    "016_add_proxy_url_to_organizations.sql": "024_add_proxy_url_to_organizations.sql",
    # Was a duplicate "017" alongside 017_disable_rls.sql, which was a strict
    # subset of this file. The subset was deleted; this file now occupies the
    # canonical "017_disable_rls.sql" slot.
    "017_disable_rls_for_workers.sql": "017_disable_rls.sql",
}
# Old filenames whose effects are entirely subsumed by another migration the
# DB has already applied (or will apply). We never re-run these.
_RETIRED_MIGRATIONS = frozenset()


async def _backfill_renamed_migration_bookkeeping(pool: asyncpg.Pool) -> None:
    """Ensure renamed migrations aren't re-applied on existing databases."""
    for old_name, new_name in _RENAMED_MIGRATIONS.items():
        old_applied = await pool.fetchval(
            "SELECT 1 FROM _migrations WHERE name = $1", old_name
        )
        if not old_applied:
            continue
        new_applied = await pool.fetchval(
            "SELECT 1 FROM _migrations WHERE name = $1", new_name
        )
        if not new_applied:
            await pool.execute(
                "INSERT INTO _migrations (name) VALUES ($1) ON CONFLICT DO NOTHING",
                new_name,
            )
            print(f"Backfilled: {new_name} (from prior {old_name})")


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

    # Reconcile prior renames before scanning the directory so existing DBs
    # don't try to re-apply a renamed file under its new name.
    await _backfill_renamed_migration_bookkeeping(pool)

    migration_dir = os.path.join(os.path.dirname(__file__), "migrations")
    for path in sorted(glob.glob(f"{migration_dir}/*.sql")):
        name = os.path.basename(path)
        if name in _RETIRED_MIGRATIONS:
            print(f"Skipped (retired): {name}")
            continue
        exists = await pool.fetchval("SELECT 1 FROM _migrations WHERE name = $1", name)
        if not exists:
            with open(path) as f:
                sql = f.read()
            await pool.execute(sql)
            await pool.execute("INSERT INTO _migrations (name) VALUES ($1)", name)
            print(f"Applied: {name}")
        else:
            print(f"Skipped (already applied): {name}")

    # Ensure admin user has a working password.
    # Uses ADMIN_PASSWORD env var (or default from settings).
    await _ensure_admin_password(pool)

    await pool.close()
    print("Migrations complete.")


async def _ensure_admin_password(pool: asyncpg.Pool) -> None:
    """Set/fix admin password from ADMIN_PASSWORD env var on every boot."""
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@lakeb2b.internal")
    admin_password = os.environ.get("ADMIN_PASSWORD", "LakeB2B_admin!")

    row = await pool.fetchrow(
        "SELECT id, password_hash, is_admin FROM users WHERE email = $1", admin_email
    )
    if not row:
        print(f"Admin user '{admin_email}' not found in DB — skipping password fix.")
        return

    current_hash = row["password_hash"]

    # Always re-hash if it's the placeholder, or if the stored hash doesn't
    # match the configured password (i.e. ADMIN_PASSWORD was changed).
    needs_update = current_hash == "REPLACE_WITH_BCRYPT_HASH"
    if not needs_update:
        try:
            needs_update = not bcrypt.checkpw(
                admin_password.encode(), current_hash.encode()
            )
        except (ValueError, TypeError):
            needs_update = True

    if needs_update:
        new_hash = bcrypt.hashpw(admin_password.encode(), bcrypt.gensalt(12)).decode()
        await pool.execute(
            "UPDATE users SET password_hash = $1 WHERE email = $2",
            new_hash, admin_email,
        )
        print(f"Admin password updated for {admin_email}.")

    # Ensure admin flag is set
    if not row["is_admin"]:
        await pool.execute(
            "UPDATE users SET is_admin = TRUE WHERE email = $1", admin_email
        )
        print(f"Admin flag set for {admin_email}.")


if __name__ == "__main__":
    asyncio.run(run_migrations())
