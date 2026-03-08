import os
from typing import AsyncGenerator

import asyncpg
import pytest
import redis.asyncio as redis

from src.config.settings import get_settings


@pytest.fixture
def sample_wordpress_html() -> str:
    return """
    <html>
    <head><title>Test Blog - WordPress</title></head>
    <body>
    <div class="wp-content">
        <article class="post">
            <h2 class="entry-title"><a href="/blog/test-post-1" rel="bookmark">Test Post 1</a></h2>
            <time class="entry-date" datetime="2024-01-15">January 15, 2024</time>
            <span class="author">John Doe</span>
        </article>
        <article class="post">
            <h2 class="entry-title"><a href="/blog/test-post-2" rel="bookmark">Test Post 2</a></h2>
            <time class="entry-date" datetime="2024-01-10">January 10, 2024</time>
            <span class="author">Jane Smith</span>
        </article>
    </div>
    <nav class="pagination">
        <a class="page-numbers" href="/blog/page/2">2</a>
        <a class="next page-numbers" href="/blog/page/2">Next</a>
    </nav>
    </body>
    </html>
    """


@pytest.fixture
def sample_team_page_html() -> str:
    return """
    <html>
    <head><title>Our Team</title></head>
    <body>
    <div class="team-section">
        <div class="team-member">
            <h3 class="name">Alice Johnson</h3>
            <p class="title">VP of Engineering</p>
            <a href="https://linkedin.com/in/alicejohnson">LinkedIn</a>
        </div>
        <div class="team-member">
            <h3 class="name">Bob Williams</h3>
            <p class="title">Director of Marketing</p>
            <a href="mailto:bob@example.com">Email</a>
        </div>
    </div>
    </body>
    </html>
    """


# ============================================================================
# Integration Test Fixtures
# ============================================================================
# These fixtures provide real infrastructure connections (DB, Redis) for
# integration tests. They're session-scoped for performance and properly
# clean up after tests complete.


@pytest.fixture(scope="session")
async def db_pool() -> AsyncGenerator[asyncpg.Pool, None]:
    """Real PostgreSQL connection pool for integration tests.

    Uses DATABASE_URL from environment or falls back to test database.
    Session-scoped for performance - shared across all integration tests.
    """
    settings = get_settings()
    database_url = os.environ.get("DATABASE_URL", settings.database_url)

    pool = await asyncpg.create_pool(database_url, min_size=2, max_size=5)
    if pool is None:
        raise RuntimeError("Failed to create database pool for integration tests")

    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture(scope="session")
async def redis_client() -> AsyncGenerator[redis.Redis, None]:
    """Real Redis client for integration tests.

    Uses REDIS_URL from environment or falls back to test Redis.
    Session-scoped for performance - shared across all integration tests.
    """
    settings = get_settings()
    redis_url = os.environ.get("REDIS_URL", settings.redis_url)

    client = await redis.from_url(redis_url)

    try:
        # Verify connection works
        await client.ping()
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def test_domains() -> list[str]:
    """List of safe, fast, stable domains for integration testing.

    These domains are:
    - Public (no authentication required)
    - Fast (respond quickly)
    - Stable (unlikely to change structure)
    - Safe (won't block/ban test traffic)
    """
    return [
        "https://example.com",  # Minimal test site (IANA example domain)
        "https://httpbin.org/html",  # Controlled test endpoint
        "https://blog.hubspot.com",  # Real B2B blog (WordPress-like)
    ]


@pytest.fixture
def test_wordpress_sites() -> list[str]:
    """WordPress/B2B blog sites for content quality testing."""
    return [
        "https://blog.hubspot.com",
        "https://www.shopify.com/blog",
        "https://blog.cloudflare.com",
    ]


@pytest.fixture
def test_contact_pages() -> list[dict[str, str]]:
    """Contact pages with known contact information for validation."""
    return [
        {
            "url": "https://example.com/contact",
            "expected_email_domain": "example.com",  # Expect @example.com emails
        },
    ]


@pytest.fixture
async def clean_test_jobs(db_pool: asyncpg.Pool) -> AsyncGenerator[None, None]:
    """Clean up test jobs after integration tests.

    Deletes any jobs created during testing to keep test database clean.
    Use this fixture for tests that create scrape_jobs records.
    """
    yield
    # Cleanup after test
    await db_pool.execute(
        "DELETE FROM scrape_jobs WHERE domain LIKE 'test-%' OR domain = 'example.com'"
    )
    await db_pool.execute("DELETE FROM scraped_data WHERE url LIKE 'https://example.com%'")


@pytest.fixture
async def clean_test_sessions(redis_client: redis.Redis) -> AsyncGenerator[None, None]:
    """Clean up test sessions after integration tests.

    Deletes any Redis sessions created during testing.
    Use this fixture for tests that create Playwright sessions.
    """
    yield
    # Cleanup after test
    keys = await redis_client.keys("playwright_session:test-*")
    keys.extend(await redis_client.keys("playwright_session:example.com"))
    if keys:
        await redis_client.delete(*keys)
