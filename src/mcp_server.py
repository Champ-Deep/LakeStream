"""
LakeStream MCP Server -- exposes scraping tools to LLMs via Model Context Protocol.

Run with:
    python -m src.mcp_server                     # stdio (Claude Desktop / Claude Code)
    python -m src.mcp_server --transport http     # streamable HTTP (production)
"""

import json
import sys
from typing import Any
from uuid import UUID

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "LakeStream",
    instructions=(
        "LakeStream is a B2B web scraping and data extraction platform.\n\n"
        "QUICK EXTRACTION (one call, no setup):\n"
        "- scrape_and_extract(url, prompt) — fetch a URL and extract anything using natural language\n"
        "- extract_blog_content(url) — get clean Markdown from any web page\n"
        "- extract_youtube_transcript(url) — get transcript from YouTube\n"
        "- extract_pdf_content(url) — extract text/tables from PDF\n\n"
        "AUTONOMOUS BROWSER (multi-step tasks):\n"
        "- browse(task, start_url) — AI agent that navigates, clicks, fills forms across pages\n\n"
        "SCHEMA-BASED EXTRACTION (precise, reusable):\n"
        "- extract_structured(url, schema, prompt) — CSS selectors OR natural language prompt\n\n"
        "ASYNC SCRAPING JOBS (full-site crawls):\n"
        "- submit_scrape_job → get_job_status → get_scrape_results\n"
        "- discover_and_scrape → get_discovery_status (search-to-scrape pipeline)\n"
    ),
)


@mcp.tool()
async def submit_scrape_job(
    domain: str,
    data_types: list[str] | None = None,
    tier: str | None = None,
    max_pages: int = 100,
    template_id: str | None = None,
    region: str | None = None,
) -> str:
    """Submit a web scraping job for a B2B domain.

    The job runs asynchronously -- use get_job_status to check progress.

    Args:
        domain: Domain to scrape (e.g. "hubspot.com", "stripe.com")
        data_types: What to extract. Options: blog_url, article, contact, tech_stack,
                    resource, pricing. Defaults to all types.
        tier: Scraping tier override: playwright, playwright_proxy.
              Leave empty for automatic escalation (recommended).
        max_pages: Maximum pages to scrape (1-500, default 100)
        template_id: Platform template: wordpress, hubspot, webflow, generic, directory.
                     Leave empty for auto-detection (recommended).
        region: Geo-target region for proxy selection: us, eu, uk, de, asia, in, au.
                Leave empty for default routing.
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
        region=region,
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
        raw_only=input_model.raw_only,
        region=input_model.region,
    )
    await redis.aclose()

    return json.dumps(
        {
            "job_id": str(job.id),
            "status": job.status,
            "domain": domain,
            "message": f"Scrape job queued for {domain}. Use get_job_status to check progress.",
        }
    )


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

    return json.dumps(
        {
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
        }
    )


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

    return json.dumps(
        {
            "discovery_id": str(disc_job.id),
            "query": query,
            "status": disc_job.status,
            "message": "Discovery job queued. Use get_discovery_status to track progress.",
        }
    )


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

        child_jobs.append(
            {
                "domain": d.domain,
                "status": d.status,
                "scrape_job_id": str(d.scrape_job_id) if d.scrape_job_id else None,
                "pages_scraped": pages_scraped,
                "cost_usd": cost_usd,
            }
        )

    return json.dumps(
        {
            "discovery_id": discovery_id,
            "query": disc_job.query,
            "status": disc_job.status,
            "domains_found": disc_job.domains_found,
            "child_jobs": child_jobs,
            "created_at": disc_job.created_at.isoformat() if disc_job.created_at else None,
            "completed_at": disc_job.completed_at.isoformat() if disc_job.completed_at else None,
        }
    )


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


@mcp.tool()
async def extract_youtube_transcript(
    url: str,
    languages: list[str] | None = None,
    include_timestamps: bool = True,
) -> str:
    """Extract the transcript from a YouTube video.

    Returns the full transcript text, timestamped segments, and video metadata
    (title, channel). Works with auto-generated and manual captions.

    Args:
        url: YouTube video URL (youtube.com/watch?v=, youtu.be/, or youtube.com/embed/)
        languages: Preferred transcript languages in priority order.
                   Defaults to ["en", "en-US", "en-GB"].
        include_timestamps: Whether to include timestamped segments in output.
                           Set to false for just the plain text. Default true.
    """
    from src.services.youtube import (
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
        extract_video_id,
        fetch_transcript,
        fetch_video_metadata,
    )

    video_id = extract_video_id(url)
    if not video_id:
        return json.dumps(
            {
                "error": (
                    "Invalid YouTube URL. "
                    "Provide a youtube.com/watch, youtu.be, or youtube.com/embed URL."
                ),
            }
        )

    # Get proxy URL from org settings (needed on cloud hosts where YouTube blocks IPs)
    proxy_url = None
    try:
        from src.db.pool import get_pool

        pool = await get_pool()
        org_id = await pool.fetchval("SELECT id FROM organizations WHERE slug = 'default'")
        if org_id:
            raw_proxy = await pool.fetchval(
                "SELECT proxy_url FROM organizations WHERE id = $1", org_id
            )
            proxy_url = raw_proxy or None
    except Exception:
        pass  # proceed without proxy if DB lookup fails

    # Fetch metadata (title, channel) — best-effort, don't fail if unavailable
    try:
        metadata = await fetch_video_metadata(video_id, proxy_url=proxy_url)
    except Exception:
        metadata = {"title": "", "channel": "", "channel_url": "", "thumbnail_url": ""}

    # Fetch transcript
    _IP_BLOCK_MARKERS = ("blocking requests from your ip", "ipblocked", "requestblocked")
    try:
        transcript_data = fetch_transcript(video_id, languages=languages, proxy_url=proxy_url)
    except (TranscriptsDisabled, NoTranscriptFound):
        return json.dumps(
            {
                "error": "No transcript available for this video. Captions may be disabled.",
                "video_id": video_id,
                "metadata": metadata,
            }
        )
    except VideoUnavailable:
        return json.dumps({"error": "Video not found or unavailable.", "video_id": video_id})
    except Exception as e:
        err_lower = str(e).lower()
        if any(marker in err_lower for marker in _IP_BLOCK_MARKERS):
            return json.dumps({
                "error": (
                    "YouTube is blocking this server's IP address. "
                    "Set a proxy URL in Settings → Proxy Configuration to route these requests."
                ),
                "video_id": video_id,
            })
        return json.dumps({"error": f"Failed to fetch transcript: {e}", "video_id": video_id})

    result: dict[str, Any] = {
        "video_id": video_id,
        "url": url,
        "metadata": metadata,
        "transcript_text": transcript_data["transcript_text"],
        "language": transcript_data["language"],
        "language_code": transcript_data["language_code"],
        "is_generated": transcript_data["is_generated"],
        "duration_seconds": transcript_data["duration_seconds"],
        "segment_count": transcript_data["segment_count"],
    }

    if include_timestamps:
        result["segments"] = transcript_data["segments"]

    return json.dumps(result)


@mcp.tool()
async def extract_blog_content(url: str) -> str:
    """Extract clean content from a blog post or web page as Markdown.

    Returns the page content as clean Markdown with metadata (title, author,
    description). Uses the same scraping engine as LakeStream's main pipeline
    with automatic anti-bot escalation.

    Args:
        url: The blog post or web page URL to extract content from.
    """
    from src.services.scraper import ScraperService

    try:
        scraper = ScraperService(escalation_service=None)
        result = await scraper.scrape(url, only_main_content=True)
    except Exception as e:
        return json.dumps(
            {
                "error": f"Failed to extract content: {e}",
                "url": url,
                "success": False,
            }
        )

    if not result.get("success"):
        return json.dumps(
            {
                "error": result.get("error", "Unknown extraction error"),
                "url": url,
                "success": False,
            }
        )

    markdown = result.get("markdown", "")
    word_count = len(markdown.split()) if markdown else 0
    reading_time_minutes = round(word_count / 238, 1)

    raw_meta = result.get("metadata", {})
    title = raw_meta.get("og_title") or raw_meta.get("title", "")
    description = raw_meta.get("og_description") or raw_meta.get("description", "")

    return json.dumps(
        {
            "url": raw_meta.get("canonical") or raw_meta.get("url", url),
            "title": title,
            "author": raw_meta.get("author", ""),
            "description": description,
            "og_image": raw_meta.get("og_image", ""),
            "word_count": word_count,
            "reading_time_minutes": reading_time_minutes,
            "markdown": markdown,
            "tier_used": result.get("tier_used", ""),
            "success": True,
        }
    )


@mcp.tool()
async def scrape_and_extract(
    url: str,
    prompt: str,
    region: str | None = None,
) -> str:
    """Fetch a URL and extract structured data using a natural language prompt.

    The simplest extraction path — no schema definition needed.
    The LLM reads the page and extracts whatever the prompt describes.
    Returns structured JSON.

    Args:
        url: The web page URL to extract from.
        prompt: Natural language description of what to extract.
                Examples:
                - "extract all plan names, prices, and features"
                - "find the CEO name, email, and LinkedIn URL"
                - "get all product names, SKUs, and prices"
        region: Optional geo-target region (us, eu, uk, asia, in, au).
    """
    from src.config.settings import get_settings
    from src.models.scraping import FetchOptions, ScrapingTier
    from src.scraping.fetcher.factory import create_fetcher
    from src.services.llm_extractor import LLMExtractor, _strip_html_to_text

    settings = get_settings()
    if not settings.openrouter_api_key:
        return json.dumps({
            "error": "AI extraction disabled — set OPENROUTER_API_KEY",
            "success": False,
        })

    options = FetchOptions(region=region)
    fetcher = create_fetcher(ScrapingTier.PLAYWRIGHT)
    fetch_result = await fetcher.fetch(url, options)

    if fetch_result.blocked or not fetch_result.html:
        return json.dumps({
            "error": f"Page blocked or empty (HTTP {fetch_result.status_code})",
            "success": False,
        })

    text = _strip_html_to_text(fetch_result.html)
    llm = LLMExtractor()
    data = await llm.extract_freeform(text, prompt)

    return json.dumps({"data": data, "url": url, "mode": "prompt", "success": True}, default=str)


@mcp.tool()
async def extract_structured(
    url: str,
    schema: dict | None = None,
    prompt: str | None = None,
    region: str | None = None,
) -> str:
    """Extract structured data from a web page.

    Supports two modes:
    1. Prompt-only (recommended): Provide just a prompt, LLM extracts freeform
    2. Schema-based: Provide a CSS-selector schema for precise, repeatable extraction

    Args:
        url: The web page URL to extract from.
        prompt: Natural language description of what to extract (no schema needed).
        schema: CSS-selector extraction schema. Example:
            {
                "name": "pricing",
                "list_selector": ".pricing-card",
                "fields": [
                    {"name": "plan", "selector": "h3", "attribute": "text"},
                    {"name": "price", "selector": ".price", "type": "number"}
                ]
            }
        region: Optional geo-target region (us, eu, uk, asia, in, au).
    """
    from src.models.scraping import FetchOptions, ScrapingTier
    from src.scraping.fetcher.factory import create_fetcher

    if not schema and not prompt:
        return json.dumps({"error": "Provide either 'schema' or 'prompt'", "success": False})

    options = FetchOptions(region=region)
    fetcher = create_fetcher(ScrapingTier.PLAYWRIGHT)
    fetch_result = await fetcher.fetch(url, options)

    if fetch_result.blocked or not fetch_result.html:
        return json.dumps({
            "error": f"Page blocked (HTTP {fetch_result.status_code})",
            "success": False,
        })

    # Prompt-only path
    if prompt and not schema:
        from src.config.settings import get_settings
        from src.services.llm_extractor import LLMExtractor, _strip_html_to_text

        settings = get_settings()
        if not settings.openrouter_api_key:
            return json.dumps({"error": "AI extraction disabled (OPENROUTER_API_KEY not set)", "success": False})

        text = _strip_html_to_text(fetch_result.html)
        llm = LLMExtractor()
        data = await llm.extract_freeform(text, prompt)
        return json.dumps({"data": data, "url": url, "mode": "prompt", "success": True}, default=str)

    # Schema path (CSS extraction)
    from src.models.extraction import ExtractionSchema
    from src.scraping.parser.schema_extractor import SchemaExtractor

    try:
        extraction_schema = ExtractionSchema(**schema)
    except Exception as e:
        return json.dumps({"error": f"Invalid schema: {e}", "success": False})

    extractor = SchemaExtractor(fetch_result.html, url)
    result = extractor.extract(extraction_schema)

    return json.dumps({
        "data": result.data,
        "schema_name": result.schema_name,
        "fields_found": result.fields_found,
        "fields_missing": result.fields_missing,
        "url": result.url,
        "mode": "css",
        "success": True,
    }, default=str)


@mcp.tool()
async def browse(
    task: str,
    start_url: str | None = None,
    max_steps: int = 20,
) -> str:
    """Use an AI browser agent to autonomously complete a multi-step web task.

    The agent can navigate pages, click buttons, fill forms, handle pagination,
    and extract data across multiple pages — like having a human browse on your behalf.
    Requires OPENROUTER_API_KEY to be set.

    Args:
        task: What to accomplish, described in natural language. Examples:
              - "find all pricing plans and features on stripe.com/pricing"
              - "search for 'B2B analytics tools' on g2.com and list the top 10 results"
              - "go to acme.com/about and find the founding year and CEO name"
        start_url: Optional starting URL. If omitted, the agent will navigate itself.
        max_steps: Maximum browser steps before stopping (default 20, max 50).
    """
    from src.config.settings import get_settings

    settings = get_settings()
    if not settings.openrouter_api_key:
        return json.dumps({
            "error": "AI browser disabled — set OPENROUTER_API_KEY",
            "success": False,
        })

    from src.services.browser_agent import run_browser_task

    try:
        result = await run_browser_task(
            task, start_url=start_url, max_steps=min(max_steps, 50)
        )
        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": str(e), "success": False})


@mcp.tool()
async def extract_pdf_content(url: str) -> str:
    """Extract text, tables, and metadata from a PDF document URL.

    Returns the PDF content as clean Markdown with extracted tables.
    Useful for processing whitepapers, reports, datasheets, and other
    B2B documents linked from company websites.

    Args:
        url: The URL of the PDF document to extract content from.
    """
    import httpx

    from src.scraping.parser.pdf_parser import parse_pdf, pdf_to_markdown

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return json.dumps({
                    "error": f"HTTP {resp.status_code}",
                    "url": url,
                    "success": False,
                })

            result = parse_pdf(resp.content)
            markdown = pdf_to_markdown(result)

            return json.dumps({
                "url": url,
                "markdown": markdown,
                "word_count": result.word_count,
                "page_count": result.page_count,
                "table_count": len(result.tables),
                "metadata": result.metadata,
                "success": True,
            })
    except Exception as e:
        return json.dumps({
            "error": str(e),
            "url": url,
            "success": False,
        })


@mcp.tool()
async def scrape_linkedin_search(
    search_url: str,
    max_pages: int = 5,
) -> str:
    """Scrape contacts from LinkedIn Sales Navigator search results.

    Requires authenticated session cookies (set up via Chrome extension
    cookie export or settings). Returns contact data including name,
    title, company, location, and LinkedIn URL.

    This is an async job — returns a job_id to poll with get_job_status.

    Args:
        search_url: Full LinkedIn Sales Navigator search URL
                    (e.g. https://www.linkedin.com/sales/search/people?query=...)
        max_pages: Maximum result pages to scrape (1-20, default 5).
                   LinkedIn rate-limits aggressively, so keep this low.
    """
    from src.config.settings import get_settings

    settings = get_settings()

    try:
        import httpx

        async with httpx.AsyncClient(base_url=settings.base_url, timeout=30) as client:
            resp = await client.post(
                "/api/scrape/linkedin",
                json={
                    "search_url": search_url,
                    "max_pages": min(max_pages, 20),
                },
            )
            data = resp.json()
            return json.dumps(data)
    except Exception as e:
        return json.dumps({"error": str(e), "success": False})


@mcp.tool()
async def scrape_apollo_search(
    search_url: str,
    max_pages: int = 10,
) -> str:
    """Scrape contacts from Apollo.io people search results.

    Requires authenticated session cookies (set up via Chrome extension
    cookie export or settings). Returns contact data including name,
    title, company, email, phone, and LinkedIn URL.

    This is an async job — returns a job_id to poll with get_job_status.

    Args:
        search_url: Full Apollo.io search URL
                    (e.g. https://app.apollo.io/#/people?...)
        max_pages: Maximum result pages to scrape (1-30, default 10).
    """
    from src.config.settings import get_settings

    settings = get_settings()

    try:
        import httpx

        async with httpx.AsyncClient(base_url=settings.base_url, timeout=30) as client:
            resp = await client.post(
                "/api/scrape/apollo",
                json={
                    "search_url": search_url,
                    "max_pages": min(max_pages, 30),
                },
            )
            data = resp.json()
            return json.dumps(data)
    except Exception as e:
        return json.dumps({"error": str(e), "success": False})


def main():
    transport = "stdio"
    if "--transport" in sys.argv:
        idx = sys.argv.index("--transport")
        if idx + 1 < len(sys.argv):
            transport = sys.argv[idx + 1]

    if transport == "http":
        import uvicorn

        port = 8001
        if "--port" in sys.argv:
            idx = sys.argv.index("--port")
            if idx + 1 < len(sys.argv):
                port = int(sys.argv[idx + 1])
        uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
