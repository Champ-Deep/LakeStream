#!/usr/bin/env python3
"""Test 3-tier architecture locally.

This script tests all three scraping tiers:
1. BASIC_HTTP - Fast HTTP requests
2. PLAYWRIGHT - Playwright with session persistence
3. PLAYWRIGHT_PROXY - Playwright + sessions + proxy

Usage:
    python scripts/test_3_tier_architecture.py

Prerequisites:
    - Redis running (docker compose up -d redis)
    - Playwright browsers installed (playwright install chromium)
    - Environment variables configured (.env file)
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models.scraping import FetchOptions, ScrapingTier
from src.scraping.fetcher.factory import create_fetcher


async def test_tier(tier: ScrapingTier, url: str) -> None:
    """Test a single tier and report results."""
    print(f"\n{'=' * 60}")
    print(f"Testing: {tier.value}")
    print(f"URL: {url}")
    print(f"{'=' * 60}")

    try:
        fetcher = create_fetcher(tier)
        result = await fetcher.fetch(url, FetchOptions())

        print(f"✓ Status: {result.status_code}")
        print(f"✓ Blocked: {result.blocked}")
        print(f"✓ HTML Size: {len(result.html)} bytes")
        print(f"✓ Duration: {result.duration_ms}ms")
        print(f"✓ Cost: ${result.cost_usd:.6f}")

        if result.blocked:
            print(f"⚠ WARNING: Fetch was blocked!")
        else:
            print(f"✓ SUCCESS: Fetch completed successfully")

        return result

    except Exception as e:
        print(f"✗ ERROR: {type(e).__name__}: {e}")
        return None


async def test_session_persistence() -> None:
    """Test session persistence across multiple requests."""
    print(f"\n{'=' * 60}")
    print("Testing: Session Persistence")
    print(f"{'=' * 60}")

    domain = "httpbin.org"
    url1 = f"https://{domain}/cookies/set?session=test123"
    url2 = f"https://{domain}/cookies"

    # First request: Set cookie
    print("\n1. Setting cookie via first request...")
    fetcher = create_fetcher(ScrapingTier.PLAYWRIGHT)
    result1 = await fetcher.fetch(url1, FetchOptions())
    print(f"✓ First request: {result1.status_code}, {result1.duration_ms}ms")

    # Second request: Verify cookie persisted
    print("\n2. Checking cookie persistence via second request...")
    result2 = await fetcher.fetch(url2, FetchOptions())
    print(f"✓ Second request: {result2.status_code}, {result2.duration_ms}ms")

    # Check if cookie is present in response
    if "session" in result2.html and "test123" in result2.html:
        print("✓ SUCCESS: Session cookie persisted across requests!")
    else:
        print("⚠ WARNING: Session cookie not found in second request")

    # Speed comparison
    if result2.duration_ms < result1.duration_ms:
        speedup = int((result1.duration_ms / result2.duration_ms) * 100) - 100
        print(f"✓ Second request was {speedup}% faster (session reuse)")


async def test_proxy_priority() -> None:
    """Test proxy priority chain (requires proxy configuration)."""
    print(f"\n{'=' * 60}")
    print("Testing: Proxy Priority Chain")
    print(f"{'=' * 60}")

    from src.config.settings import get_settings

    settings = get_settings()

    # Check proxy configuration
    proxies_configured = []
    if settings.custom_proxy_url:
        proxies_configured.append("custom")
    if settings.brightdata_proxy_url:
        proxies_configured.append("brightdata")
    if settings.smartproxy_url:
        proxies_configured.append("smartproxy")

    if not proxies_configured:
        print("⚠ No proxies configured (skipping proxy tests)")
        print("  Set CUSTOM_PROXY_URL, BRIGHTDATA_PROXY_URL, or SMARTPROXY_URL to test proxies")
        return

    print(f"✓ Proxies configured: {', '.join(proxies_configured)}")

    # Test with proxy
    url = "https://httpbin.org/ip"
    fetcher = create_fetcher(ScrapingTier.PLAYWRIGHT_PROXY)
    result = await fetcher.fetch(url, FetchOptions())

    if result and not result.blocked:
        print(f"✓ Proxy fetch succeeded: {result.status_code}")
        print(f"  IP info: {result.html[:200]}...")
    else:
        print(f"⚠ Proxy fetch failed or blocked")


async def test_tier_migration() -> None:
    """Test tier migration from deprecated tiers."""
    print(f"\n{'=' * 60}")
    print("Testing: Tier Migration (Backward Compatibility)")
    print(f"{'=' * 60}")

    from src.services.escalation import _TIER_MIGRATION_MAP

    print(f"✓ Migration map configured:")
    for old_tier, new_tier in _TIER_MIGRATION_MAP.items():
        print(f"  {old_tier} → {new_tier.value}")


async def main():
    """Run all tests."""
    print("=" * 60)
    print("LakeStream 3-Tier Architecture Test Suite")
    print("=" * 60)

    # Test URLs (simple, publicly accessible)
    test_urls = [
        "https://httpbin.org/html",  # Simple HTML page
        "https://example.com",  # Minimal page
    ]

    # Test each tier on each URL
    print("\n" + "=" * 60)
    print("TIER FUNCTIONALITY TESTS")
    print("=" * 60)

    for url in test_urls:
        print(f"\n\nTesting URL: {url}")
        print("-" * 60)

        # Test BASIC_HTTP
        await test_tier(ScrapingTier.BASIC_HTTP, url)

        # Test PLAYWRIGHT
        await test_tier(ScrapingTier.PLAYWRIGHT, url)

        # Test PLAYWRIGHT_PROXY
        await test_tier(ScrapingTier.PLAYWRIGHT_PROXY, url)

    # Test session persistence
    await test_session_persistence()

    # Test proxy priority
    await test_proxy_priority()

    # Test tier migration
    await test_tier_migration()

    print("\n" + "=" * 60)
    print("TEST SUITE COMPLETE")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Check Redis for sessions: redis-cli KEYS 'playwright_session:*'")
    print("2. Verify session TTL: redis-cli TTL playwright_session:httpbin.org")
    print("3. Run full test suite: pytest tests/unit/ -v")


if __name__ == "__main__":
    asyncio.run(main())
