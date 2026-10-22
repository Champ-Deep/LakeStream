"""First-class web search API backed by LakeCurrent.

POST /api/search returns ranked results synchronously; with scrape=true the
top results are fetched and returned with clean markdown attached. This is
also the sanctioned fallback for pipeline domain-resolution when public
search engines rate-limit (see the enrichment pipeline runbook).
"""

import asyncio

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.middleware.auth import get_current_user
from src.config.settings import get_settings
from src.services.lakecurrent import LakeCurrentClient, SearchResult

router = APIRouter(prefix="/search")
logger = structlog.get_logger()

# Bound the inline-scrape fan-out so one request can't monopolize fetchers
MAX_INLINE_SCRAPES = 5


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=400)
    limit: int = Field(default=10, ge=1, le=50)
    pages: int = Field(default=1, ge=1, le=5)
    mode: str = Field(default="auto", pattern="^(auto|filter|glimpse)$")
    language: str | None = None
    scrape: bool = False
    scrape_top: int = Field(default=MAX_INLINE_SCRAPES, ge=1, le=MAX_INLINE_SCRAPES)


class SearchApiResult(SearchResult):
    markdown: str | None = None


class SearchApiResponse(BaseModel):
    success: bool = True
    query: str
    results: list[SearchApiResult]
    suggestions: list[str] = []
    answers: list[str] = []


async def _scrape_result(result: SearchApiResult) -> None:
    """Attach markdown to a single search result; failures degrade to None."""
    from src.models.scraping import FetchOptions, ScrapingTier
    from src.scraping.fetcher.factory import create_fetcher
    from src.scraping.parser.markdown import html_to_markdown

    settings = get_settings()
    tier = (
        ScrapingTier.GO_HTTP
        if settings.enable_go_fetchers and settings.go_http_fetcher_url
        else ScrapingTier.PLAYWRIGHT
    )
    try:
        fetcher = create_fetcher(tier)
        fetch_result = await fetcher.fetch(result.url, FetchOptions(tier=tier))
        if fetch_result.html and not fetch_result.blocked:
            result.markdown = html_to_markdown(fetch_result.html, max_chars=20000)
    except Exception as e:
        logger.debug("search_inline_scrape_failed", url=result.url, error=str(e))


@router.post("", response_model=SearchApiResponse)
async def web_search(
    input: SearchRequest, user: dict = Depends(get_current_user)
) -> SearchApiResponse:
    """Ranked web search, optionally with inline markdown scraping."""
    settings = get_settings()
    if not settings.enable_search_api or not settings.lakecurrent_enabled:
        raise HTTPException(status_code=503, detail="Search API is disabled")

    client = LakeCurrentClient(settings.lakecurrent_base_url, settings.lakecurrent_timeout)
    try:
        if input.pages > 1:
            raw = await client.search_pages(
                input.query, pages=input.pages, per_page=input.limit, mode=input.mode
            )
            results = [SearchApiResult(**r.model_dump()) for r in raw]
            suggestions: list[str] = []
            answers: list[str] = []
        else:
            resp = await client.search(
                input.query, mode=input.mode, limit=input.limit, language=input.language
            )
            results = [SearchApiResult(**r.model_dump()) for r in resp.results]
            suggestions = resp.suggestions
            answers = resp.answers
    except Exception as e:
        logger.warning("lakecurrent_search_failed", query=input.query, error=str(e))
        raise HTTPException(status_code=502, detail=f"Search backend error: {e}")
    finally:
        await client.close()

    if input.scrape and results:
        await asyncio.gather(
            *(_scrape_result(r) for r in results[: input.scrape_top])
        )

    return SearchApiResponse(
        query=input.query, results=results, suggestions=suggestions, answers=answers
    )
