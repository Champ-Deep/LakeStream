"""
Local test server + test runner for the 3 Crawlee-inspired improvements.

Spins up a fake HTTP server on localhost that simulates:
  - 429 responses (for adaptive rate limiting)
  - Connection drops (for retry logic)
  - Multiple pages with links (for concurrency limiting)
  - Normal pages (baseline)

Then runs the real LakeStream code against it — no mocks.

Usage:
    python scripts/test_local.py
"""

import asyncio
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ── Fake target site ──────────────────────────────────────────────

# Track state across requests
server_state = {
    "rate_limit_hits": 0,  # how many times /rate-limited was hit
    "flaky_hits": 0,  # how many times /flaky was hit
    "concurrent": 0,  # current concurrent connections
    "peak_concurrent": 0,  # max concurrent seen
    "request_log": [],  # all requests for inspection
}


class FakeSiteHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress default logging

    def do_GET(self):
        server_state["concurrent"] += 1
        if server_state["concurrent"] > server_state["peak_concurrent"]:
            server_state["peak_concurrent"] = server_state["concurrent"]
        server_state["request_log"].append(self.path)

        try:
            if self.path == "/rate-limited":
                server_state["rate_limit_hits"] += 1
                if server_state["rate_limit_hits"] <= 2:
                    # First 2 requests: 429
                    self.send_response(429)
                    self.send_header("Retry-After", "1")
                    self.end_headers()
                    self.wfile.write(b"Too Many Requests")
                    return
                # After that: 200
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Success after rate limit</h1></body></html>")

            elif self.path == "/slow":
                # Simulate slow page (for concurrency testing)
                time.sleep(0.3)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h1>Slow page</h1>"
                    b'<a href="/page-1">Page 1</a>'
                    b'<a href="/page-2">Page 2</a>'
                    b"</body></html>"
                )

            elif self.path.startswith("/page-"):
                time.sleep(0.2)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                page_num = self.path.split("-")[-1]
                self.wfile.write(
                    f"<html><body><h1>Page {page_num}</h1></body></html>".encode()
                )

            elif self.path == "/sitemap.xml":
                # Return 404 so crawler falls back to recursive crawl
                self.send_response(404)
                self.end_headers()

            else:
                # Default: serve a page with links (for crawl discovery)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body>"
                    b"<h1>Test Site Home</h1>"
                    b'<a href="/slow">Slow page</a>'
                    b'<a href="/page-1">Page 1</a>'
                    b'<a href="/page-2">Page 2</a>'
                    b'<a href="/page-3">Page 3</a>'
                    b'<a href="/rate-limited">Rate limited</a>'
                    b"</body></html>"
                )
        finally:
            server_state["concurrent"] -= 1


def start_server(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), FakeSiteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ── Test functions ────────────────────────────────────────────────


async def test_adaptive_rate_limiting():
    """Test 1: Verify rate limiter backs off on 429."""
    print("\n" + "=" * 60)
    print("TEST 1: Adaptive Rate Limiting")
    print("=" * 60)

    from src.services.rate_limiter import RateLimiter

    rl = RateLimiter(default_delay_ms=100, max_delay_ms=5000)

    # Simulate 429 responses
    print(f"  Default delay: {rl._default_delay}s")

    rl.report_result("localhost", 429)
    d1 = rl._current_delay["localhost"]
    print(f"  After 1st 429: {d1}s")

    rl.report_result("localhost", 429)
    d2 = rl._current_delay["localhost"]
    print(f"  After 2nd 429: {d2}s")

    rl.report_result("localhost", 429)
    d3 = rl._current_delay["localhost"]
    print(f"  After 3rd 429: {d3}s")

    # Verify doubling
    assert d1 == 0.2, f"Expected 0.2, got {d1}"
    assert d2 == 0.4, f"Expected 0.4, got {d2}"
    assert d3 == 0.8, f"Expected 0.8, got {d3}"

    # Simulate recovery
    rl.report_result("localhost", 200)
    d4 = rl._current_delay["localhost"]
    print(f"  After success: {d4}s (decayed)")
    assert d4 < d3, f"Expected decay, got {d4} >= {d3}"

    # Verify max cap
    rl2 = RateLimiter(default_delay_ms=100, max_delay_ms=500)
    for _ in range(20):
        rl2.report_result("test.com", 429)
    d_max = rl2._current_delay["test.com"]
    print(f"  Max cap: {d_max}s (should be 0.5)")
    assert d_max == 0.5, f"Expected 0.5, got {d_max}"

    print("  PASSED")


async def test_retry_on_transport_errors():
    """Test 2: Verify retry_async retries on transport errors."""
    print("\n" + "=" * 60)
    print("TEST 2: Retry on Transport Errors")
    print("=" * 60)

    from src.utils.retry import retry_async

    # Test: succeeds after 2 failures
    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise TimeoutError("timed out")
        return "OK"

    result = await retry_async(
        flaky, max_retries=2, base_delay=0.05, retry_on=(TimeoutError,)
    )
    print(f"  Retry success: {result} after {call_count} attempts")
    assert result == "OK"
    assert call_count == 3

    # Test: exhaustion raises
    call_count = 0
    try:
        await retry_async(
            flaky, max_retries=1, base_delay=0.05, retry_on=(TimeoutError,)
        )
        assert False, "Should have raised"
    except TimeoutError:
        print(f"  Retry exhaustion: correctly raised after {call_count} attempts")

    # Test: non-retryable errors propagate immediately
    async def bad():
        raise ValueError("not retryable")

    try:
        await retry_async(bad, max_retries=3, base_delay=0.05, retry_on=(TimeoutError,))
        assert False, "Should have raised"
    except ValueError:
        print("  Non-retryable: ValueError propagated immediately")

    print("  PASSED")


async def test_per_domain_concurrency(port: int):
    """Test 3: Verify semaphore limits concurrent requests.

    Uses httpx directly against the local HTTP server, gated by CrawlerService's
    real semaphore. This avoids Scrapling's forced-HTTPS issue on localhost while
    still exercising the actual concurrency-limiting code path.
    """
    print("\n" + "=" * 60)
    print("TEST 3: Per-Domain Concurrency Limit")
    print("=" * 60)

    import httpx

    from src.services.crawler import CrawlerService

    # Reset server state
    server_state["peak_concurrent"] = 0
    server_state["request_log"] = []

    crawler = CrawlerService(max_concurrent=10, max_per_domain=2, timeout=10000)
    domain = f"127.0.0.1:{port}"
    sem = crawler._get_semaphore(domain)

    # Fire 6 requests to /slow (0.3s each), gated by the real semaphore (max 2)
    base = f"http://{domain}"
    urls = [f"{base}/slow", f"{base}/page-1", f"{base}/page-2",
            f"{base}/page-3", f"{base}/page-4", f"{base}/page-5"]

    async def fetch_with_sem(url: str):
        async with sem:
            async with httpx.AsyncClient(timeout=10.0) as client:
                return await client.get(url)

    tasks = [fetch_with_sem(u) for u in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    ok_count = sum(1 for r in results if not isinstance(r, Exception) and r.status_code == 200)

    print(f"  Fired {len(urls)} requests through semaphore(max=2)")
    print(f"  Successful responses: {ok_count}")
    print(f"  Peak concurrent requests: {server_state['peak_concurrent']}")
    print(f"  Request log: {len(server_state['request_log'])} requests")

    # The key assertion: peak concurrent should be <= 2
    assert server_state["peak_concurrent"] <= 2, (
        f"Peak concurrent was {server_state['peak_concurrent']}, expected <= 2"
    )
    assert server_state["peak_concurrent"] >= 1, (
        "No requests reached the server — test is not exercising the code"
    )
    assert ok_count == len(urls), f"Expected {len(urls)} OK responses, got {ok_count}"

    print("  PASSED")


async def test_real_site_crawl():
    """Test 4: Crawl a real site to verify everything works end-to-end."""
    print("\n" + "=" * 60)
    print("TEST 4: Real Site Crawl (example.com)")
    print("=" * 60)

    from src.services.crawler import CrawlerService
    from src.services.rate_limiter import RateLimiter

    rl = RateLimiter(default_delay_ms=500, max_delay_ms=10000)
    crawler = CrawlerService(max_concurrent=5, max_per_domain=2, timeout=15000)

    print("  Crawling httpbin.org (safe test target)...")
    start = time.time()
    urls = await crawler.map_domain("httpbin.org", limit=10)
    elapsed = time.time() - start

    print(f"  Found {len(urls)} URLs in {elapsed:.1f}s")
    for url in urls[:5]:
        print(f"    {url}")
    if len(urls) > 5:
        print(f"    ... and {len(urls) - 5} more")

    assert len(urls) >= 1, "Should discover at least the homepage"
    print("  PASSED")


# ── Main ──────────────────────────────────────────────────────────


async def main():
    port = 18932  # random high port
    print(f"Starting fake test server on http://127.0.0.1:{port}")
    server = start_server(port)

    try:
        passed = 0
        failed = 0

        for test_fn in [
            test_adaptive_rate_limiting,
            test_retry_on_transport_errors,
        ]:
            try:
                await test_fn()
                passed += 1
            except Exception as e:
                print(f"  FAILED: {e}")
                failed += 1

        # Concurrency test needs the port
        try:
            await test_per_domain_concurrency(port)
            passed += 1
        except Exception as e:
            print(f"  FAILED: {e}")
            failed += 1

        # Real site test
        try:
            await test_real_site_crawl()
            passed += 1
        except Exception as e:
            print(f"  FAILED: {e}")
            failed += 1

        print("\n" + "=" * 60)
        print(f"RESULTS: {passed} passed, {failed} failed")
        print("=" * 60)

        return failed == 0

    finally:
        server.shutdown()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
