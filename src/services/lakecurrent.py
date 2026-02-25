"""HTTP client for the LakeCurrent search API."""

from urllib.parse import urlparse

import httpx
import structlog
from pydantic import BaseModel

log = structlog.get_logger()


class SearchResult(BaseModel):
    url: str
    title: str
    snippet: str
    engine: str | None = None
    score: float | None = None
    published_date: str | None = None
    domain: str  # extracted from url


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    suggestions: list[str] = []
    answers: list[str] = []


class LakeCurrentClient:
    """Async client for LakeCurrent search API."""

    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def search(
        self,
        query: str,
        *,
        mode: str = "auto",
        pageno: int = 1,
        limit: int = 10,
        categories: str | None = None,
        language: str | None = None,
    ) -> SearchResponse:
        """Execute a single search query against LakeCurrent."""
        params: dict[str, str | int] = {
            "q": query,
            "mode": mode,
            "pageno": pageno,
            "limit": limit,
        }
        if categories:
            params["categories"] = categories
        if language:
            params["language"] = language

        r = await self._client.get(f"{self.base_url}/search", params=params)
        r.raise_for_status()
        data = r.json()

        results: list[SearchResult] = []
        for item in data.get("results", []):
            parsed = urlparse(item["url"])
            results.append(
                SearchResult(
                    url=item["url"],
                    title=item.get("title", ""),
                    snippet=item.get("snippet", ""),
                    engine=item.get("engine"),
                    score=item.get("score"),
                    published_date=item.get("published_date"),
                    domain=parsed.netloc.removeprefix("www."),
                )
            )

        return SearchResponse(
            query=data.get("query", query),
            results=results,
            suggestions=data.get("suggestions", []),
            answers=data.get("answers", []),
        )

    async def search_pages(
        self,
        query: str,
        *,
        pages: int = 3,
        per_page: int = 10,
        mode: str = "auto",
    ) -> list[SearchResult]:
        """Fetch multiple pages and return all results as a flat list."""
        all_results: list[SearchResult] = []
        for page in range(1, pages + 1):
            resp = await self.search(query, mode=mode, pageno=page, limit=per_page)
            all_results.extend(resp.results)
            if len(resp.results) < per_page:
                break  # no more results
        log.info(
            "lakecurrent_search_complete",
            query=query,
            pages_fetched=min(page, pages),
            total_results=len(all_results),
        )
        return all_results

    async def health(self) -> dict:
        """Check LakeCurrent health status."""
        r = await self._client.get(f"{self.base_url}/health")
        return r.json()

    async def close(self) -> None:
        await self._client.aclose()
