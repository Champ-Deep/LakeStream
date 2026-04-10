from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request

from src.db.pool import get_pool
from src.db.queries import jobs as job_queries
from src.db.queries import scraped_data as data_queries
from src.models.api import ExecuteScrapeResponse, ScrapeStatusResponse
from src.models.job import ScrapeJobInput

logger = structlog.get_logger()

router = APIRouter(prefix="/scrape")


@router.post("/execute", status_code=202, response_model=ExecuteScrapeResponse)
async def execute_scrape(input: ScrapeJobInput, request: Request) -> ExecuteScrapeResponse:
    pool = await get_pool()

    # Get org_id and user_id from authenticated user (set by auth middleware)
    org_id_str = getattr(request.state, "org_id", None)
    org_id = UUID(org_id_str) if org_id_str else None
    user_id_str = getattr(request.state, "user_id", None)
    user_id = UUID(user_id_str) if user_id_str else None

    # Create job record
    job = await job_queries.create_job(pool, input, org_id=org_id, user_id=user_id)

    # Enqueue arq job
    try:
        from arq.connections import RedisSettings
        from arq.connections import create_pool as create_arq_pool

        from src.config.settings import get_settings

        settings = get_settings()
        redis = await create_arq_pool(RedisSettings.from_dsn(settings.redis_url))
        await redis.enqueue_job(
            "process_scrape_job",
            job_id=str(job.id),
            domain=input.domain,
            template_id=input.template_id or "auto",
            max_pages=input.max_pages,
            data_types=input.data_types,
            tier=input.tier,
            region=input.region,
        )
        await redis.aclose()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to enqueue job: {e}")

    return ExecuteScrapeResponse(
        job_id=job.id,
        status=job.status,
        message=f"Scrape job queued for {input.domain}",
    )


@router.post("/cancel/{job_id}")
async def cancel_scrape_job(job_id: UUID, request: Request):
    """Cancel a pending or running scrape job."""
    pool = await get_pool()
    cancelled = await job_queries.cancel_job(pool, job_id)
    if not cancelled:
        job = await job_queries.get_job(pool, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel job in '{job.status}' state",
        )
    return {"success": True, "job_id": str(job_id), "status": "cancelled"}


@router.get("/status/{job_id}", response_model=ScrapeStatusResponse)
async def get_status(job_id: UUID) -> ScrapeStatusResponse:
    pool = await get_pool()
    job = await job_queries.get_job(pool, job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    data_count = await data_queries.count_scraped_data_by_job(pool, job_id)

    return ScrapeStatusResponse(
        job_id=job.id,
        domain=job.domain,
        status=job.status,
        strategy_used=job.strategy_used,
        pages_scraped=job.pages_scraped,
        cost_usd=job.cost_usd,
        duration_ms=job.duration_ms,
        created_at=job.created_at.isoformat(),
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        error_message=job.error_message,
        data_count=data_count,
    )


@router.post("/linkedin", status_code=202, response_model=ExecuteScrapeResponse)
async def scrape_linkedin(request: Request) -> ExecuteScrapeResponse:
    """Scrape LinkedIn Sales Navigator search results. Async job-based.

    Body: {search_url: str, max_pages: int = 5, session_cookies: list[dict] | None}
    """
    pool = await get_pool()
    body = await request.json()
    search_url = body.get("search_url", "").strip()
    max_pages = body.get("max_pages", 5)
    session_cookies = body.get("session_cookies")

    if not search_url:
        raise HTTPException(status_code=400, detail="search_url is required")

    org_id_str = getattr(request.state, "org_id", None)
    org_id = UUID(org_id_str) if org_id_str else None
    user_id_str = getattr(request.state, "user_id", None)
    user_id = UUID(user_id_str) if user_id_str else None

    from src.models.job import ScrapeJobInput

    input_data = ScrapeJobInput(
        domain="linkedin.com",
        data_types=["contact"],
        max_pages=max_pages,
    )
    job = await job_queries.create_job(pool, input_data, org_id=org_id, user_id=user_id)

    try:
        from arq.connections import RedisSettings
        from arq.connections import create_pool as create_arq_pool

        from src.config.settings import get_settings

        settings = get_settings()
        redis = await create_arq_pool(RedisSettings.from_dsn(settings.redis_url))
        await redis.enqueue_job(
            "process_linkedin_scrape_job",
            job_id=str(job.id),
            search_url=search_url,
            max_pages=max_pages,
            session_cookies=session_cookies,
        )
        await redis.aclose()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to enqueue job: {e}")

    return ExecuteScrapeResponse(
        job_id=job.id,
        status=job.status,
        message=f"LinkedIn scrape job queued ({max_pages} pages)",
    )


@router.post("/apollo", status_code=202, response_model=ExecuteScrapeResponse)
async def scrape_apollo(request: Request) -> ExecuteScrapeResponse:
    """Scrape Apollo.io people search results. Async job-based.

    Body: {search_url: str, max_pages: int = 10, session_cookies: list[dict] | None}
    """
    pool = await get_pool()
    body = await request.json()
    search_url = body.get("search_url", "").strip()
    max_pages = body.get("max_pages", 10)
    session_cookies = body.get("session_cookies")

    if not search_url:
        raise HTTPException(status_code=400, detail="search_url is required")

    org_id_str = getattr(request.state, "org_id", None)
    org_id = UUID(org_id_str) if org_id_str else None
    user_id_str = getattr(request.state, "user_id", None)
    user_id = UUID(user_id_str) if user_id_str else None

    from src.models.job import ScrapeJobInput

    input_data = ScrapeJobInput(
        domain="apollo.io",
        data_types=["contact"],
        max_pages=max_pages,
    )
    job = await job_queries.create_job(pool, input_data, org_id=org_id, user_id=user_id)

    try:
        from arq.connections import RedisSettings
        from arq.connections import create_pool as create_arq_pool

        from src.config.settings import get_settings

        settings = get_settings()
        redis = await create_arq_pool(RedisSettings.from_dsn(settings.redis_url))
        await redis.enqueue_job(
            "process_apollo_scrape_job",
            job_id=str(job.id),
            search_url=search_url,
            max_pages=max_pages,
            session_cookies=session_cookies,
        )
        await redis.aclose()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to enqueue job: {e}")

    return ExecuteScrapeResponse(
        job_id=job.id,
        status=job.status,
        message=f"Apollo scrape job queued ({max_pages} pages)",
    )


@router.post("/extract")
async def extract_structured(request: Request):
    """Extract structured data from a URL.

    Body: {
      url: str,
      prompt?: str,       # Natural language — what to extract (no schema needed)
      schema?: {...},     # CSS-selector schema (optional if prompt provided)
      region?: str,
      mode?: "css"|"ai"|"auto"|"prompt",
      instructions?: str  # Extra hints for AI modes
    }

    Modes:
    - prompt: LLM extracts freeform from a natural language description (no schema required)
    - css: CSS selector-based, fast, deterministic (requires schema)
    - ai: LLM-powered via OpenRouter (requires schema)
    - auto: try CSS first, fallback to AI if <50% fields found (requires schema)
    """
    body = await request.json()
    url = body.get("url", "").strip()
    schema_data = body.get("schema")
    prompt = body.get("prompt", "").strip()
    region = body.get("region")
    mode = body.get("mode", "prompt" if not body.get("schema") else "css")
    instructions = body.get("instructions", "")

    if not url:
        return {"success": False, "error": "url is required"}
    if not schema_data and not prompt:
        return {"success": False, "error": "Either 'schema' or 'prompt' is required"}

    # Fetch the page
    from src.models.scraping import FetchOptions, ScrapingTier
    from src.scraping.fetcher.factory import create_fetcher

    options = FetchOptions(region=region)
    fetcher = create_fetcher(ScrapingTier.PLAYWRIGHT)
    fetch_result = await fetcher.fetch(url, options)

    if fetch_result.blocked:
        return {"success": False, "error": f"Page blocked (HTTP {fetch_result.status_code})"}
    if not fetch_result.html or len(fetch_result.html) < 100:
        return {"success": False, "error": "No content retrieved"}

    # Prompt-only mode: freeform LLM extraction (no schema needed)
    if mode == "prompt" or (prompt and not schema_data):
        from src.services.llm_extractor import LLMExtractor, _strip_html_to_text, get_openrouter_config

        org_id = getattr(request.state, "org_id", None)
        try:
            await get_openrouter_config(org_id)
        except ValueError:
            return {"success": False, "error": "AI extraction disabled — configure an API key in Settings → AI Extraction"}

        text = _strip_html_to_text(fetch_result.html)
        llm = LLMExtractor(org_id=org_id)
        data = await llm.extract_freeform(text, prompt or instructions)
        return {"success": True, "data": data, "mode": "prompt", "url": url}

    # Schema-based extraction path (css / ai / auto)
    from src.models.extraction import ExtractionSchema
    from src.scraping.parser.schema_extractor import SchemaExtractor

    try:
        schema = ExtractionSchema(**schema_data)
    except Exception as e:
        return {"success": False, "error": f"Invalid schema: {e}"}

    result = None
    mode_used = mode

    # CSS extraction
    if mode in ("css", "auto"):
        extractor = SchemaExtractor(fetch_result.html, url)
        result = extractor.extract(schema)
        mode_used = "css"

        # Auto mode: fallback to AI if <50% fields found
        if mode == "auto" and len(schema.fields) > 0:
            coverage = result.fields_found / len(schema.fields)
            if coverage < 0.5:
                mode_used = "ai"
                result = None

    # AI extraction (mode=ai, or auto fallback)
    if result is None and mode in ("ai", "auto"):
        from src.services.llm_extractor import LLMExtractor, get_openrouter_config

        org_id = getattr(request.state, "org_id", None)
        try:
            await get_openrouter_config(org_id)
        except ValueError:
            return {"success": False, "error": "AI extraction disabled — configure an API key in Settings → AI Extraction"}

        llm = LLMExtractor(org_id=org_id)
        result = await llm.extract_from_html(fetch_result.html, schema, instructions)
        result.url = url
        mode_used = "ai"

    if result is None:
        return {"success": False, "error": "No extraction result"}

    return {
        "success": True,
        "data": result.data,
        "schema_name": result.schema_name,
        "fields_found": result.fields_found,
        "fields_missing": result.fields_missing,
        "mode": mode_used,
        "url": result.url,
    }


@router.post("/browse")
async def browse_with_agent(request: Request):
    """Run an AI browser agent for a multi-step autonomous web task.

    Body: {task: str, start_url?: str, max_steps?: int}
    Returns: {success: bool, result: str, steps_taken: int, urls_visited: list[str]}

    The agent can navigate, click buttons, fill forms, paginate, and extract data
    across multiple pages. Requires OPENROUTER_API_KEY to be set.
    """
    from src.services.llm_extractor import get_openrouter_config

    body = await request.json()
    task = body.get("task", "").strip()
    start_url = body.get("start_url", "").strip() or None
    max_steps = min(int(body.get("max_steps", 20)), 50)

    if not task:
        return {"success": False, "error": "task is required"}

    org_id = getattr(request.state, "org_id", None)
    try:
        await get_openrouter_config(org_id)
    except ValueError:
        return {"success": False, "error": "AI browser disabled — configure an API key in Settings → AI Extraction"}

    from src.services.browser_agent import run_browser_task

    try:
        return await run_browser_task(task, start_url=start_url, max_steps=max_steps, org_id=org_id)
    except Exception as e:
        logger.error("browser_agent_failed", task=task[:100], error=str(e))
        return {"success": False, "error": f"Browser agent failed: {e}"}


@router.post("/pdf")
async def extract_pdf(request: Request):
    """Extract text, tables, and metadata from a PDF URL. Returns immediately (no job queue)."""
    body = await request.json()
    url = body.get("url", "").strip()

    if not url:
        return {"success": False, "error": "URL is required"}

    import httpx

    from src.scraping.parser.pdf_parser import parse_pdf, pdf_to_markdown

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return {"success": False, "error": f"HTTP {resp.status_code}"}

            content_type = resp.headers.get("content-type", "")
            if "pdf" not in content_type and not url.lower().endswith(".pdf"):
                return {"success": False, "error": f"Not a PDF (content-type: {content_type})"}

            result = parse_pdf(resp.content)
            markdown = pdf_to_markdown(result)

            return {
                "success": True,
                "url": url,
                "text": result.text,
                "markdown": markdown,
                "tables": result.tables,
                "metadata": result.metadata,
                "word_count": result.word_count,
                "page_count": result.page_count,
                "table_count": len(result.tables),
            }
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error("pdf_extraction_failed", url=url, error=str(e))
        return {"success": False, "error": f"Failed to extract PDF: {e}"}


@router.post("/session-cookies")
async def store_session_cookies(request: Request):
    """Store authenticated session cookies from Chrome extension for server-side scraping.

    Body: {domain: str, cookies: list[dict]}
    """
    body = await request.json()
    domain = body.get("domain", "").strip()
    cookies = body.get("cookies", [])

    if not domain or not cookies:
        raise HTTPException(status_code=400, detail="domain and cookies are required")

    from src.services.session_manager import AuthenticatedSessionManager

    mgr = AuthenticatedSessionManager()
    await mgr.create_session(domain, cookies)

    return {
        "success": True,
        "domain": domain,
        "cookie_count": len(cookies),
        "message": f"Session stored for {domain} — server-side scraping ready",
    }


@router.post("/youtube-transcript")
async def youtube_transcript(request: Request):
    """Extract transcript from a YouTube video URL. Returns immediately (no job queue)."""
    from src.db.pool import get_pool
    from src.services.youtube import (
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
        extract_video_id,
        fetch_transcript,
        fetch_video_metadata,
    )

    body = await request.json()
    url = body.get("url", "").strip()
    include_timestamps = body.get("include_timestamps", True)
    languages = body.get("languages")

    if not url:
        return {"success": False, "error": "URL is required"}

    video_id = extract_video_id(url)
    if not video_id:
        return {"success": False, "error": "Invalid YouTube URL"}

    # Get proxy URL from org settings (needed on cloud hosts where YouTube blocks IPs)
    proxy_url = None
    try:
        pool = await get_pool()
        org_id = getattr(request.state, "org_id", None)
        if not org_id:
            org_id = await pool.fetchval("SELECT id FROM organizations WHERE slug = 'default'")
        raw_proxy = await pool.fetchval(
            "SELECT proxy_url FROM organizations WHERE id = $1", org_id
        )
        proxy_url = raw_proxy or None
        if not proxy_url:
            logger.warning("youtube_no_proxy_configured", video_id=video_id, org_id=str(org_id))
    except Exception as e:
        logger.warning("youtube_proxy_lookup_failed", video_id=video_id, error=str(e))

    # Fetch metadata (best-effort)
    metadata = {"title": "", "channel": "", "channel_url": "", "thumbnail_url": ""}
    try:
        metadata = await fetch_video_metadata(video_id, proxy_url=proxy_url)
    except Exception as e:
        logger.warning("youtube_metadata_failed", video_id=video_id, error=str(e))

    # Fetch transcript
    _IP_BLOCK_MARKERS = ("blocking requests from your ip", "ipblocked", "requestblocked")
    try:
        transcript_data = fetch_transcript(video_id, languages=languages, proxy_url=proxy_url)
    except TranscriptsDisabled:
        return {
            "success": False,
            "error": "No transcript available — captions are disabled for this video",
            "metadata": metadata,
            "video_id": video_id,
        }
    except (NoTranscriptFound, VideoUnavailable) as e:
        return {"success": False, "error": str(e), "video_id": video_id}
    except Exception as e:
        err_lower = str(e).lower()
        if any(marker in err_lower for marker in _IP_BLOCK_MARKERS):
            logger.warning("youtube_ip_blocked", video_id=video_id, proxy_configured=bool(proxy_url))
            msg = (
                "YouTube is blocking this server's IP address. "
                "To fix this, set a proxy URL in Settings → Proxy Configuration."
            )
            return {"success": False, "error": msg, "video_id": video_id, "metadata": metadata}
        logger.error("youtube_transcript_failed", video_id=video_id, error=str(e))
        return {"success": False, "error": f"Failed to fetch transcript: {e}"}

    result = {
        "success": True,
        "video_id": video_id,
        "metadata": metadata,
        "transcript_text": transcript_data["transcript_text"],
        "segment_count": transcript_data["segment_count"],
        "language": transcript_data["language"],
        "language_code": transcript_data["language_code"],
        "is_generated": transcript_data["is_generated"],
        "duration_seconds": transcript_data["duration_seconds"],
    }

    if include_timestamps:
        result["segments"] = transcript_data["segments"]

    return result
