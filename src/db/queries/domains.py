import asyncpg

from src.models.domain_metadata import DomainMetadata


async def get_domain_metadata(pool: asyncpg.Pool, domain: str) -> DomainMetadata | None:
    row = await pool.fetchrow("SELECT * FROM domain_metadata WHERE domain = $1", domain)
    if row is None:
        return None
    return DomainMetadata(**dict(row))


async def upsert_domain_metadata(
    pool: asyncpg.Pool,
    domain: str,
    *,
    last_successful_strategy: str | None = None,
    block_count_increment: int = 0,
    success_rate: float | None = None,
    avg_cost_usd: float | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO domain_metadata (
            domain, last_successful_strategy, block_count,
            last_scraped_at, success_rate, avg_cost_usd
        )
        VALUES ($1, $2, $3, NOW(), $4, $5)
        ON CONFLICT (domain) DO UPDATE SET
            last_successful_strategy = COALESCE($2, domain_metadata.last_successful_strategy),
            block_count = domain_metadata.block_count + $3,
            last_scraped_at = NOW(),
            success_rate = COALESCE($4, domain_metadata.success_rate),
            avg_cost_usd = COALESCE($5, domain_metadata.avg_cost_usd),
            updated_at = NOW()
        """,
        domain,
        last_successful_strategy,
        block_count_increment,
        success_rate,
        avg_cost_usd,
    )


async def list_domains(
    pool: asyncpg.Pool,
    *,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "last_scraped_at",
) -> list[DomainMetadata]:
    allowed_sorts = {"last_scraped_at", "success_rate", "domain", "avg_cost_usd"}
    if sort_by not in allowed_sorts:
        sort_by = "last_scraped_at"

    order = "DESC" if sort_by != "domain" else "ASC"
    rows = await pool.fetch(
        f"SELECT * FROM domain_metadata ORDER BY {sort_by} {order} NULLS LAST LIMIT $1 OFFSET $2",
        limit,
        offset,
    )
    return [DomainMetadata(**dict(row)) for row in rows]
