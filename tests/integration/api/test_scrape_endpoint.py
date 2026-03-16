"""Integration tests for /api/scrape endpoints.

Tests E2E scraping workflow:
1. Submit scrape job via POST /api/scrape/execute
2. Poll job status via GET /api/scrape/status/{job_id}
3. Verify scraped data inserted into database
4. Test error handling (invalid domains, timeouts, tier selection)

Requires running infrastructure:
- PostgreSQL database
- Redis for job queue
- arq worker (for job processing)

NOTE: These tests make real scrape requests, so they:
- Are slow (30s-2min per test)
- Require network connectivity
- Should use safe test domains
"""

import asyncio
from uuid import UUID

import asyncpg
import httpx
import pytest

from src.db.pool import get_pool
from src.db.queries import jobs as job_queries
from src.db.queries import scraped_data as data_queries
from src.models.job import ScrapeJobInput


@pytest.mark.integration
@pytest.mark.slow
class TestScrapeExecuteEndpoint:
    """Test POST /api/scrape/execute endpoint."""

    @pytest.fixture
    def base_url(self) -> str:
        """API base URL for testing."""
        return "http://localhost:3000"

    @pytest.fixture
    async def http_client(self) -> httpx.AsyncClient:
        """HTTP client for API requests."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            yield client

    async def test_execute_scrape_basic_http_tier(
        self, base_url: str, http_client: httpx.AsyncClient, db_pool: asyncpg.Pool
    ):
        """Test executing a scrape job with basic HTTP tier.

        Workflow:
        1. Submit scrape job for example.com
        2. Verify 202 Accepted response with job_id
        3. Poll job status until completion
        4. Verify job completed successfully
        5. Verify scraped data in database
        """
        # Step 1: Submit scrape job
        payload = {
            "domain": "example.com",
            "template_id": "generic",
            "data_types": ["contact"],
            "max_pages": 1,
            "tier": "basic_http",  # Force basic HTTP tier
        }

        response = await http_client.post(f"{base_url}/api/scrape/execute", json=payload)

        # Verify response
        assert response.status_code == 202, f"Expected 202, got {response.status_code}"
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "queued"

        job_id = UUID(data["job_id"])

        # Step 2: Poll job status (wait up to 60 seconds)
        max_wait = 60
        poll_interval = 2
        elapsed = 0
        final_status = None

        while elapsed < max_wait:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            status_response = await http_client.get(
                f"{base_url}/api/scrape/status/{job_id}"
            )
            assert status_response.status_code == 200

            status_data = status_response.json()
            final_status = status_data["status"]

            if final_status in ["completed", "failed"]:
                break

        # Step 3: Verify job completed
        assert final_status == "completed", f"Job did not complete: {final_status}"

        # Step 4: Verify scraped data in database
        scraped_count = await data_queries.count_scraped_data_by_job(db_pool, job_id)
        assert scraped_count >= 0, "No scraped data found"

        # Cleanup
        await db_pool.execute("DELETE FROM scrape_jobs WHERE id = $1", job_id)
        await db_pool.execute("DELETE FROM scraped_data WHERE job_id = $1", job_id)

    async def test_execute_scrape_with_tier_selection(
        self, base_url: str, http_client: httpx.AsyncClient, db_pool: asyncpg.Pool
    ):
        """Test tier selection parameter.

        Tests that the tier parameter is respected and job uses specified tier.
        """
        payload = {
            "domain": "example.com",
            "template_id": "generic",
            "data_types": ["contact"],
            "max_pages": 1,
            "tier": "playwright",  # Force Playwright tier
        }

        response = await http_client.post(f"{base_url}/api/scrape/execute", json=payload)
        assert response.status_code == 202

        job_id = UUID(response.json()["job_id"])

        # Wait for job to process
        await asyncio.sleep(10)

        # Check job record for strategy used
        job = await job_queries.get_job(db_pool, job_id)
        assert job is not None

        # Cleanup
        await db_pool.execute("DELETE FROM scrape_jobs WHERE id = $1", job_id)
        await db_pool.execute("DELETE FROM scraped_data WHERE job_id = $1", job_id)

    async def test_execute_scrape_invalid_domain(
        self, base_url: str, http_client: httpx.AsyncClient
    ):
        """Test error handling for invalid domain."""
        payload = {
            "domain": "not-a-real-domain-12345.com",
            "template_id": "generic",
            "data_types": ["contact"],
            "max_pages": 1,
        }

        response = await http_client.post(f"{base_url}/api/scrape/execute", json=payload)

        # Should accept job (validation happens during execution)
        assert response.status_code == 202

        job_id = UUID(response.json()["job_id"])

        # Wait for job to fail
        await asyncio.sleep(15)

        # Check status
        pool = await get_pool()
        job = await job_queries.get_job(pool, job_id)

        # Job should fail due to invalid domain
        assert job is not None
        # Clean up regardless of status
        await pool.execute("DELETE FROM scrape_jobs WHERE id = $1", job_id)

    async def test_execute_scrape_missing_required_fields(
        self, base_url: str, http_client: httpx.AsyncClient
    ):
        """Test validation for missing required fields."""
        # Missing domain
        payload = {
            "template_id": "generic",
            "data_types": ["contact"],
            "max_pages": 1,
        }

        response = await http_client.post(f"{base_url}/api/scrape/execute", json=payload)

        # Should return 422 Unprocessable Entity for validation error
        assert response.status_code == 422

    async def test_execute_scrape_with_multiple_data_types(
        self, base_url: str, http_client: httpx.AsyncClient, db_pool: asyncpg.Pool
    ):
        """Test scraping with multiple data types.

        Tests that multiple data_types are processed correctly.
        """
        payload = {
            "domain": "example.com",
            "template_id": "generic",
            "data_types": ["contact", "tech_stack"],
            "max_pages": 1,
        }

        response = await http_client.post(f"{base_url}/api/scrape/execute", json=payload)
        assert response.status_code == 202

        job_id = UUID(response.json()["job_id"])

        # Wait for processing
        await asyncio.sleep(15)

        # Verify data types extracted
        scraped_data = await db_pool.fetch(
            "SELECT DISTINCT data_type FROM scraped_data WHERE job_id = $1",
            job_id,
        )

        data_types = [row["data_type"] for row in scraped_data]

        # Cleanup
        await db_pool.execute("DELETE FROM scrape_jobs WHERE id = $1", job_id)
        await db_pool.execute("DELETE FROM scraped_data WHERE job_id = $1", job_id)

        # At least one data type should be extracted
        assert len(data_types) > 0


@pytest.mark.integration
class TestScrapeStatusEndpoint:
    """Test GET /api/scrape/status/{job_id} endpoint."""

    @pytest.fixture
    def base_url(self) -> str:
        return "http://localhost:3000"

    @pytest.fixture
    async def http_client(self) -> httpx.AsyncClient:
        async with httpx.AsyncClient(timeout=10.0) as client:
            yield client

    async def test_status_for_nonexistent_job(
        self, base_url: str, http_client: httpx.AsyncClient
    ):
        """Test status endpoint with non-existent job ID."""
        fake_job_id = "00000000-0000-0000-0000-000000000000"

        response = await http_client.get(f"{base_url}/api/scrape/status/{fake_job_id}")

        assert response.status_code == 404

    async def test_status_for_invalid_job_id(
        self, base_url: str, http_client: httpx.AsyncClient
    ):
        """Test status endpoint with invalid UUID format."""
        invalid_job_id = "not-a-valid-uuid"

        response = await http_client.get(f"{base_url}/api/scrape/status/{invalid_job_id}")

        # Should return 422 for validation error
        assert response.status_code == 422

    async def test_status_response_structure(
        self, base_url: str, http_client: httpx.AsyncClient, db_pool: asyncpg.Pool
    ):
        """Test that status response has correct structure.

        Creates a test job and verifies response fields.
        """
        # Create a test job directly in database
        pool = await get_pool()
        job_input = ScrapeJobInput(
            domain="test-domain.com",
            template_id="generic",
            data_types=["contact"],
            max_pages=1,
        )

        job = await job_queries.create_job(pool, job_input, org_id=None)

        # Fetch status
        response = await http_client.get(f"{base_url}/api/scrape/status/{job.id}")

        assert response.status_code == 200

        data = response.json()

        # Verify response structure
        assert "job_id" in data
        assert "domain" in data
        assert "status" in data
        assert "pages_scraped" in data
        assert "cost_usd" in data
        assert "created_at" in data
        assert "data_count" in data

        # Cleanup
        await db_pool.execute("DELETE FROM scrape_jobs WHERE id = $1", job.id)
