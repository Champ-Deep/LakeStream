"""
LakeStream MCP Server -- exposes scraping tools to LLMs via Model Context Protocol.

Run with:
    python -m src.mcp_server                     # stdio (Claude Desktop / Claude Code)
    python -m src.mcp_server --transport http     # streamable HTTP (production)
"""

import json
import sys
from uuid import UUID

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "LakeStream",
    instructions=(
        "LakeStream is a B2B web scraping platform. Use these tools to scrape domains "
        "for blog posts, articles, contacts, tech stacks, pricing, and resources. "
        "Submit a scrape job, then poll its status until complete, then get results."
    ),
)


@mcp.tool()
async def submit_scrape_job(
    domain: str,
    data_types: list[str] | None = None,
    tier: str | None = None,
    max_pages: int = 100,
    template_id: str | None = None,
) -> str:
    """Submit a web scraping job for a B2B domain.

    The job runs asynchronously -- use get_job_status to check progress.

    Args:
        domain: Domain to scrape (e.g. "hubspot.com", "stripe.com")
        data_types: What to extract. Options: blog_url, article, contact, tech_stack,
                    resource, pricing. Defaults to all types.
        tier: Scraping tier override: basic_http, playwright, playwright_proxy.
              Leave empty for automatic escalation (recommended).
        max_pages: Maximum pages to scrape (1-500, default 100)
        template_id: Platform template: wordpress, hubspot, webflow, generic, directory.
                     Leave empty for auto-detection (recommended).
    """
    from src.config.settings import get_settings
    from src.db.pool import get_pool
    from src.db.queries.jobs import create_job
    from src.models.job import ScrapeJobInput

    if data_types is None:
        data_types = ["blog_url", "article", "contact", "tech_stack", "resource", "pricing"]

    input_model = ScrapeJobInput(
        domain=domain,
        data_types=data_types,
        tier=tier,
        max_pages=max_pages,
        template_id=template_id,
    )

    pool = await get_pool()
    job = await create_job(pool, input_model, org_id=None)

    # Enqueue to arq
    from arq.connections import RedisSettings
    from arq.connections import create_pool as create_arq_pool

    settings = get_settings()
    redis = await create_arq_pool(RedisSettings.from_dsn(settings.redis_url))
    await redis.enqueue_job(
        "process_scrape_job",
        job_id=str(job.id),
        domain=input_model.domain,
        template_id=input_model.template_id or "auto",
        max_pages=input_model.max_pages,
        data_types=input_model.data_types,
        tier=input_model.tier,
    )
    await redis.aclose()

    return json.dumps({
        "job_id": str(job.id),
        "status": job.status,
        "domain": domain,
        "message": f"Scrape job queued for {domain}. Use get_job_status to check progress.",
    })


@mcp.tool()
async def get_job_status(job_id: str) -> str:
    """Check the status of a scrape job.

    Poll this until status is 'completed' or 'failed'.

    Args:
        job_id: The UUID of the scrape job (from submit_scrape_job)
    """
    from src.db.pool import get_pool
    from src.db.queries.jobs import get_job
    from src.db.queries.scraped_data import count_scraped_data_by_job

    pool = await get_pool()
    job = await get_job(pool, UUID(job_id))

    if job is None:
        return json.dumps({"error": "Job not found"})

    data_count = await count_scraped_data_by_job(pool, job.id)

    return json.dumps({
        "job_id": str(job.id),
        "domain": job.domain,
        "status": job.status,
        "strategy_used": job.strategy_used,
        "pages_scraped": job.pages_scraped,
        "data_count": data_count,
        "cost_usd": job.cost_usd,
        "duration_ms": job.duration_ms,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    })


@mcp.tool()
async def get_scrape_results(
    job_id: str,
    data_type: str | None = None,
    limit: int = 50,
) -> str:
    """Get extracted data from a completed scrape job.

    Args:
        job_id: The UUID of the scrape job
        data_type: Filter by type: blog_url, article, contact, tech_stack, resource, pricing.
                   Leave empty for all types.
        limit: Maximum records to return (default 50, max 200)
    """
    from src.db.pool import get_pool
    from src.db.queries.scraped_data import get_scraped_data_by_job

    pool = await get_pool()
    data = await get_scraped_data_by_job(pool, UUID(job_id))

    if not data:
        return json.dumps({"error": "No data found for this job"})

    if data_type:
        data = [d for d in data if d.data_type == data_type]

    data = data[: min(limit, 200)]

    records = [
        {
            "data_type": item.data_type,
            "url": item.url,
            "title": item.title,
            "published_date": str(item.published_date) if item.published_date else None,
            "metadata": item.metadata or {},
        }
        for item in data
    ]

    return json.dumps({"job_id": job_id, "total_records": len(records), "data": records})


@mcp.tool()
async def discover_and_scrape(
    query: str,
    data_types: list[str] | None = None,
    search_pages: int = 3,
    max_pages_per_domain: int = 50,
) -> str:
    """Search for B2B domains matching a query and automatically scrape them.

    Uses LakeCurrent search to find relevant domains, then queues scrape jobs.

    Args:
        query: Search query (e.g. "marketing automation SaaS", "healthcare IT companies")
        data_types: What to extract from each domain. Defaults to all types.
        search_pages: Number of search result pages (1-10, default 3)
        max_pages_per_domain: Max pages to scrape per domain (1-500, default 50)
    """
    from src.config.settings import get_settings
    from src.db.pool import get_pool
    from src.db.queries.discovery import create_discovery_job
    from src.models.discovery import DiscoveryJobInput

    if data_types is None:
        data_types = ["blog_url", "article", "contact", "tech_stack", "resource", "pricing"]

    input_model = DiscoveryJobInput(
        query=query,
        data_types=data_types,
        search_pages=search_pages,
        max_pages_per_domain=max_pages_per_domain,
    )

    pool = await get_pool()
    disc_job = await create_discovery_job(pool, input_model, org_id=None)

    from arq.connections import RedisSettings
    from arq.connections import create_pool as create_arq_pool

    settings = get_settings()
    redis = await create_arq_pool(RedisSettings.from_dsn(settings.redis_url))
    await redis.enqueue_job("process_discovery_job", discovery_id=str(disc_job.id))
    await redis.aclose()

    return json.dumps({
        "discovery_id": str(disc_job.id),
        "query": query,
        "status": disc_job.status,
        "message": "Discovery job queued. Use get_discovery_status to track progress.",
    })


@mcp.tool()
async def get_discovery_status(discovery_id: str) -> str:
    """Check the status of a search-and-scrape discovery job.

    Args:
        discovery_id: The UUID of the discovery job (from discover_and_scrape)
    """
    from src.db.pool import get_pool
    from src.db.queries.discovery import get_discovery_domains, get_discovery_job
    from src.db.queries.jobs import get_job

    pool = await get_pool()
    disc_job = await get_discovery_job(pool, UUID(discovery_id))

    if disc_job is None:
        return json.dumps({"error": "Discovery job not found"})

    domains = await get_discovery_domains(pool, UUID(discovery_id))

    child_jobs = []
    for d in domains:
        pages_scraped = 0
        cost_usd = 0.0
        if d.scrape_job_id:
            scrape_job = await get_job(pool, d.scrape_job_id)
            if scrape_job:
                pages_scraped = scrape_job.pages_scraped
                cost_usd = scrape_job.cost_usd or 0.0

        child_jobs.append({
            "domain": d.domain,
            "status": d.status,
            "scrape_job_id": str(d.scrape_job_id) if d.scrape_job_id else None,
            "pages_scraped": pages_scraped,
            "cost_usd": cost_usd,
        })

    return json.dumps({
        "discovery_id": discovery_id,
        "query": disc_job.query,
        "status": disc_job.status,
        "domains_found": disc_job.domains_found,
        "child_jobs": child_jobs,
        "created_at": disc_job.created_at.isoformat() if disc_job.created_at else None,
        "completed_at": disc_job.completed_at.isoformat() if disc_job.completed_at else None,
    })


@mcp.tool()
async def list_scraped_domains(limit: int = 20) -> str:
    """List domains that have been previously scraped with stats.

    Args:
        limit: Number of domains to return (default 20, max 100)
    """
    from src.db.pool import get_pool
    from src.db.queries.domains import list_domains

    pool = await get_pool()
    domains = await list_domains(pool, limit=min(limit, 100))

    records = [
        {
            "domain": d.domain,
            "last_strategy": d.last_successful_strategy,
            "success_rate": d.success_rate,
            "block_count": d.block_count,
            "avg_cost_usd": d.avg_cost_usd,
            "last_scraped_at": d.last_scraped_at.isoformat() if d.last_scraped_at else None,
        }
        for d in domains
    ]

    return json.dumps({"total": len(records), "domains": records})


@mcp.tool()
async def list_templates() -> str:
    """List available scraping templates (WordPress, HubSpot, Webflow, etc.)."""
    from src.templates.registry import list_templates as get_all_templates

    templates = get_all_templates()

    records = [{"id": t.id, "name": t.name, "description": t.description} for t in templates]

    return json.dumps({"templates": records})


def main():
    transport = "stdio"
    if "--transport" in sys.argv:
        idx = sys.argv.index("--transport")
        if idx + 1 < len(sys.argv):
            transport = sys.argv[idx + 1]

    if transport == "http":
        mcp.run(transport="streamable-http", host="0.0.0.0", port=8001)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
