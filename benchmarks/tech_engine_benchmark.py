"""Throughput benchmark for the technology detection engine.

Answers the only question that matters for a large run: how long will N
companies take in the *detection* stage (excluding network fetch)?

    python -m benchmarks.tech_engine_benchmark            # built-in catalog
    TECH_CATALOG_PATH=/path/to/catalog \
        python -m benchmarks.tech_engine_benchmark 200000

As a CI/wave gate, --max-ms-per-page makes the run fail (exit 1) when the
per-page detection cost exceeds the budget:

    python -m benchmarks.tech_engine_benchmark --max-ms-per-page 300

The engine matches each signature only against the small field it targets
(script URLs, a named header, a cookie, a meta tag), with a literal prefilter
in front of the regex. That is what keeps a multi-thousand-signature catalog
viable — a naive "run every pattern over the whole HTML" approach is roughly
three orders of magnitude slower.
"""

import random
import string
import sys
import time

from src.scraping.parser.tech_engine import (
    detect,
    extract_page_signals,
    get_catalog,
)

SAMPLE_HEADERS = {
    "Server": "nginx/1.24.0 (Ubuntu)",
    "X-Powered-By": "PHP/8.2.1",
    "CF-RAY": "8a1b2c3d4e5f-LHR",
    "Content-Type": "text/html; charset=UTF-8",
    "Set-Cookie": "PHPSESSID=abc123; Path=/, _ga=GA1.2.3; Path=/",
}


def build_page(size_kb: int = 200) -> str:
    """A realistic page: real markup up top, bulk filler body."""
    random.seed(42)
    head = """<html><head>
<meta name="generator" content="WordPress 6.4.2">
<meta property="og:site_name" content="Example">
<script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
<script src="https://www.googletagmanager.com/gtm.js?id=GTM-XYZ"></script>
<script src="https://cdn.segment.com/analytics.js/v1/abc/analytics.min.js"></script>
<script src="https://js.stripe.com/v3/"></script>
<script src="https://widget.intercom.io/widget/abc123"></script>
<link rel="stylesheet" href="/wp-content/themes/x/style.css">
</head><body>"""
    filler = "".join(random.choices(string.ascii_lowercase + " ", k=size_kb * 1024))
    return head + filler + "</body></html>"


def main() -> None:
    args = sys.argv[1:]
    max_ms: float | None = None
    if "--max-ms-per-page" in args:
        idx = args.index("--max-ms-per-page")
        max_ms = float(args[idx + 1])
        args = args[:idx] + args[idx + 2:]
    target_n = int(args[0]) if args else 100_000
    iterations = 300

    catalog = get_catalog()
    html = build_page()

    # Warm up (first call builds/compiles the catalog).
    signals = extract_page_signals(html, url="https://example.com", headers=SAMPLE_HEADERS)
    detect(signals)

    t0 = time.perf_counter()
    for _ in range(iterations):
        s = extract_page_signals(html, url="https://example.com", headers=SAMPLE_HEADERS)
        detect(s)
    elapsed = time.perf_counter() - t0

    per_page_ms = elapsed / iterations * 1000
    pages_per_sec = iterations / elapsed
    total_hours = (per_page_ms / 1000) * target_n / 3600

    found = detect(signals)
    print(f"catalog signatures : {catalog.size}")
    print(f"page size          : {len(html) // 1024} KB")
    print(f"iterations         : {iterations}")
    print(f"per page           : {per_page_ms:.2f} ms")
    print(f"throughput         : {pages_per_sec:,.0f} pages/sec (single core)")
    print(f"detections/page    : {len(found)}")
    print()
    print(f"projected for {target_n:,} pages, detection only:")
    print(f"  1 core           : {total_hours:.2f} h")
    print(f"  8 cores          : {total_hours / 8:.2f} h")
    print()
    print("Note: excludes network fetch, which dominates a real run and is")
    print("bounded by concurrency + politeness, not by this stage.")

    if max_ms is not None and per_page_ms > max_ms:
        print(f"\nFAIL: {per_page_ms:.2f} ms/page exceeds the {max_ms:.0f} ms budget.")
        sys.exit(1)


if __name__ == "__main__":
    main()
