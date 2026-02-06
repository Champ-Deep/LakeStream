"""Seed development data."""

import asyncio
import os

import asyncpg


async def seed() -> None:
    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://scraper:scraper_dev@localhost:5432/lakeb2b_scraper",
    )
    pool = await asyncpg.create_pool(database_url)
    assert pool is not None

    # Seed some domain_metadata for testing
    test_domains = [
        ("example.com", "basic_http", 0, 0.95, 0.0001),
        ("hubspot.com", "headless_browser", 2, 0.85, 0.002),
        ("cloudflare.com", "headless_proxy", 5, 0.75, 0.004),
    ]

    for domain, strategy, blocks, rate, cost in test_domains:
        await pool.execute(
            """
            INSERT INTO domain_metadata (
                domain, last_successful_strategy, block_count,
                success_rate, avg_cost_usd
            )
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (domain) DO NOTHING
            """,
            domain,
            strategy,
            blocks,
            rate,
            cost,
        )

    await pool.close()
    print("Seed data inserted.")


if __name__ == "__main__":
    asyncio.run(seed())
