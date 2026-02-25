"""Unit tests for the discovery pipeline (LakeCurrent client, domain extractor, models)."""

from unittest.mock import patch

import httpx
import pytest

from src.models.discovery import (
    DiscoveryJobInput,
    DiscoveryStatus,
    TrackedSearchInput,
)
from src.services.domain_extractor import extract_unique_domains
from src.services.lakecurrent import LakeCurrentClient, SearchResult

# --------------- LakeCurrentClient ---------------

MOCK_SEARCH_RESPONSE = {
    "query": "insurtech startups",
    "results": [
        {
            "url": "https://www.example.com/about",
            "title": "Example InsurTech",
            "snippet": "Leading insurance technology provider...",
            "engine": "google",
            "score": 3.5,
            "published_date": "2024-06-15",
        },
        {
            "url": "https://acme.io/blog/post",
            "title": "Acme Blog Post",
            "snippet": "Acme does insurtech stuff",
            "engine": "bing",
            "score": 2.0,
        },
    ],
    "suggestions": ["insurtech companies 2024"],
    "answers": [],
}


def _mock_response(status_code: int, **kwargs) -> httpx.Response:
    """Create a mock httpx.Response with a request attached."""
    request = httpx.Request("GET", "http://test")
    return httpx.Response(status_code, request=request, **kwargs)


async def test_lakecurrent_search():
    """Test single search call parses results and extracts domains."""
    mock_response = _mock_response(200, json=MOCK_SEARCH_RESPONSE)

    with patch.object(httpx.AsyncClient, "get", return_value=mock_response):
        client = LakeCurrentClient(base_url="http://localhost:8001")
        resp = await client.search("insurtech startups")
        await client.close()

    assert resp.query == "insurtech startups"
    assert len(resp.results) == 2
    assert resp.results[0].domain == "example.com"
    assert resp.results[0].title == "Example InsurTech"
    assert resp.results[1].domain == "acme.io"
    assert resp.suggestions == ["insurtech companies 2024"]


async def test_lakecurrent_search_strips_www():
    """Test that www. prefix is stripped from domains."""
    data = {
        "query": "test",
        "results": [{"url": "https://www.bigcorp.com/page", "title": "Big Corp", "snippet": "x"}],
        "suggestions": [],
        "answers": [],
    }
    mock_response = _mock_response(200, json=data)

    with patch.object(httpx.AsyncClient, "get", return_value=mock_response):
        client = LakeCurrentClient(base_url="http://localhost:8001")
        resp = await client.search("test")
        await client.close()

    assert resp.results[0].domain == "bigcorp.com"


async def test_lakecurrent_search_pages():
    """Test multi-page search fetches until exhausted."""
    page1 = {
        "query": "test",
        "results": [
            {"url": "https://a.com/1", "title": "A", "snippet": "a"},
            {"url": "https://b.com/1", "title": "B", "snippet": "b"},
        ],
        "suggestions": [],
        "answers": [],
    }
    page2 = {
        "query": "test",
        "results": [
            {"url": "https://c.com/1", "title": "C", "snippet": "c"},
        ],
        "suggestions": [],
        "answers": [],
    }

    responses = iter([_mock_response(200, json=page1), _mock_response(200, json=page2)])

    with patch.object(httpx.AsyncClient, "get", side_effect=lambda *a, **kw: next(responses)):
        client = LakeCurrentClient(base_url="http://localhost:8001")
        results = await client.search_pages("test", pages=3, per_page=2)
        await client.close()

    # Should stop after page 2 (1 result < per_page of 2)
    assert len(results) == 3
    assert results[0].domain == "a.com"
    assert results[2].domain == "c.com"


async def test_lakecurrent_health():
    """Test health check call."""
    health_data = {"status": "healthy", "components": {"LakeFilter": "ok"}}
    mock_response = _mock_response(200, json=health_data)

    with patch.object(httpx.AsyncClient, "get", return_value=mock_response):
        client = LakeCurrentClient(base_url="http://localhost:8001")
        result = await client.health()
        await client.close()

    assert result["status"] == "healthy"


async def test_lakecurrent_search_http_error():
    """Test that HTTP errors are raised."""
    mock_response = _mock_response(502, text="Bad Gateway")

    with patch.object(httpx.AsyncClient, "get", return_value=mock_response):
        client = LakeCurrentClient(base_url="http://localhost:8001")
        with pytest.raises(httpx.HTTPStatusError):
            await client.search("test")
        await client.close()


# --------------- Domain Extractor ---------------


def _make_result(domain: str, url: str = "", score: float | None = None) -> SearchResult:
    return SearchResult(
        url=url or f"https://{domain}/page",
        title=f"Title for {domain}",
        snippet=f"Snippet for {domain}",
        domain=domain,
        score=score,
    )


def test_extract_unique_domains_basic():
    """Test basic deduplication picks highest score per domain."""
    results = [
        _make_result("example.com", score=2.0),
        _make_result("example.com", url="https://example.com/other", score=5.0),
        _make_result("acme.io", score=3.0),
    ]
    domain_map = extract_unique_domains(results)

    assert len(domain_map) == 2
    assert domain_map["example.com"].score == 5.0
    assert domain_map["acme.io"].score == 3.0


def test_extract_unique_domains_skip():
    """Test that skip_domains filters out domains."""
    results = [
        _make_result("example.com", score=2.0),
        _make_result("acme.io", score=3.0),
        _make_result("blocked.com", score=4.0),
    ]
    domain_map = extract_unique_domains(results, skip_domains={"blocked.com", "other.com"})

    assert len(domain_map) == 2
    assert "blocked.com" not in domain_map


def test_extract_unique_domains_none_scores():
    """Test that None scores are treated as 0."""
    results = [
        _make_result("example.com", score=None),
        _make_result("example.com", url="https://example.com/better", score=1.0),
    ]
    domain_map = extract_unique_domains(results)

    assert domain_map["example.com"].score == 1.0


def test_extract_unique_domains_empty():
    """Test empty input returns empty dict."""
    assert extract_unique_domains([]) == {}


# --------------- Model Validation ---------------


def test_discovery_job_input_defaults():
    """Test DiscoveryJobInput applies correct defaults."""
    input = DiscoveryJobInput(query="test query", data_types=["contact"])

    assert input.search_mode == "auto"
    assert input.search_pages == 3
    assert input.results_per_page == 10
    assert input.template_id == "generic"
    assert input.max_pages_per_domain == 50
    assert input.priority == 5


def test_discovery_job_input_validation():
    """Test DiscoveryJobInput rejects invalid values."""
    with pytest.raises(Exception):
        DiscoveryJobInput(query="", data_types=["contact"])  # empty query

    with pytest.raises(Exception):
        DiscoveryJobInput(query="test", data_types=[])  # empty data_types

    with pytest.raises(Exception):
        DiscoveryJobInput(
            query="test",
            data_types=["contact"],
            search_pages=20,  # exceeds max 10
        )


def test_tracked_search_input_defaults():
    """Test TrackedSearchInput applies correct defaults."""
    input = TrackedSearchInput(query="weekly search", data_types=["tech_stack"])

    assert input.scrape_frequency == "weekly"
    assert input.search_pages == 2
    assert input.webhook_url is None


def test_tracked_search_input_invalid_frequency():
    """Test TrackedSearchInput rejects invalid frequency."""
    with pytest.raises(Exception):
        TrackedSearchInput(query="test", data_types=["contact"], scrape_frequency="hourly")


def test_discovery_status_enum():
    """Test DiscoveryStatus enum values."""
    assert DiscoveryStatus.SEARCHING == "searching"
    assert DiscoveryStatus.SCRAPING == "scraping"
    assert DiscoveryStatus.COMPLETED == "completed"
    assert DiscoveryStatus.FAILED == "failed"
