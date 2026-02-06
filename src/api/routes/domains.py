from fastapi import APIRouter, HTTPException

from src.db.pool import get_pool
from src.db.queries import domains as domain_queries
from src.models.domain_metadata import DomainMetadata

router = APIRouter(prefix="/domains")


@router.get("", response_model=list[DomainMetadata])
async def list_domains(
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "last_scraped_at",
) -> list[DomainMetadata]:
    pool = await get_pool()
    return await domain_queries.list_domains(pool, limit=limit, offset=offset, sort_by=sort_by)


@router.get("/{domain}/stats")
async def get_domain_stats(domain: str) -> DomainMetadata:
    pool = await get_pool()
    meta = await domain_queries.get_domain_metadata(pool, domain)
    if meta is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    return meta
