"""Run SQL migrations from db/migrations/ in sorted order."""

import asyncio
import glob
import os

import asyncpg


async def run_migrations() -> None:
    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://scraper:scraper_dev@localhost:5433/lakeb2b_scraper",
    )
    pool = await asyncpg.create_pool(database_url)
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

    await pool.close()
    print("Migrations complete.")


if __name__ == "__main__":
    asyncio.run(run_migrations())
