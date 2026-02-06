import asyncpg

from src.db.queries.scraped_data import batch_insert_scraped_data


async def export_to_postgres(
    pool: asyncpg.Pool,
    records: list[dict],
) -> int:
    """Export scraped data records to Postgres."""
    return await batch_insert_scraped_data(pool, records)
