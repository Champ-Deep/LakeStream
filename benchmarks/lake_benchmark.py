#!/usr/bin/env python3
"""Benchmark script to test all 3 Lake scraping tiers against a list of domains."""

import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from src.models.scraping import ScrapingTier
from src.scraping.fetcher.factory import create_fetcher


@dataclass
class BenchmarkResult:
    """Result of a single benchmark run."""

    domain: str
    tier: str
    status_code: int
    success: bool
    blocked: bool
    captcha_detected: bool
    duration_ms: int
    html_length: int
    error: str | None = None


@dataclass
class BenchmarkSummary:
    """Summary of benchmark results."""

    tier: str
    total: int = 0
    success_count: int = 0
    blocked_count: int = 0
    captcha_count: int = 0
    avg_duration_ms: float = 0.0
    total_duration_ms: int = 0
    results: list[BenchmarkResult] = field(default_factory=list)


DEFAULT_TEST_DOMAINS = [
    "https://example.com",
    "https://httpbin.org/html",
    "https://www.python.org",
]


async def run_benchmark(
    domain: str,
    tier: ScrapingTier,
    timeout: int = 30000,
) -> BenchmarkResult:
    """Run a single benchmark for a domain and tier."""
    fetcher = create_fetcher(tier)

    start = time.time()
    try:
        result = await fetcher.fetch(domain)
        duration_ms = int((time.time() - start) * 1000)

        return BenchmarkResult(
            domain=domain,
            tier=tier.value,
            status_code=result.status_code,
            success=not result.blocked and result.status_code == 200,
            blocked=result.blocked,
            captcha_detected=result.captcha_detected,
            duration_ms=duration_ms,
            html_length=len(result.html),
        )
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        return BenchmarkResult(
            domain=domain,
            tier=tier.value,
            status_code=0,
            success=False,
            blocked=True,
            captcha_detected=False,
            duration_ms=duration_ms,
            html_length=0,
            error=str(e),
        )


async def run_benchmarks(
    domains: list[str],
    tiers: list[ScrapingTier] | None = None,
) -> dict[str, BenchmarkSummary]:
    """Run benchmarks for all domains and tiers."""
    if tiers is None:
        tiers = [
            ScrapingTier.BASIC_HTTP,
            ScrapingTier.HEADLESS_BROWSER,
            ScrapingTier.HEADLESS_PROXY,
        ]

    summaries: dict[str, BenchmarkSummary] = {}

    for tier in tiers:
        summary = BenchmarkSummary(tier=tier.value)
        print(f"\n{'=' * 60}")
        print(f"Testing Tier: {tier.value}")
        print(f"{'=' * 60}")

        for domain in domains:
            print(f"  Fetching {domain}...", end=" ", flush=True)
            result = await run_benchmark(domain, tier)
            summary.results.append(result)
            summary.total += 1
            summary.total_duration_ms += result.duration_ms

            if result.success:
                summary.success_count += 1
                print(f"OK ({result.duration_ms}ms)")
            elif result.captcha_count:
                summary.captcha_count += 1
                print(f"CAPTCHA ({result.duration_ms}ms)")
            elif result.blocked:
                summary.blocked_count += 1
                print(f"BLOCKED ({result.duration_ms}ms)")
            else:
                print(f"ERROR: {result.error}")

        summary.avg_duration_ms = (
            summary.total_duration_ms / summary.total if summary.total > 0 else 0
        )
        summaries[tier.value] = summary

    return summaries


def print_results(summaries: dict[str, BenchmarkSummary]) -> None:
    """Print benchmark results in a table format."""
    print("\n" + "=" * 80)
    print("BENCHMARK RESULTS SUMMARY")
    print("=" * 80)

    for tier_value, summary in summaries.items():
        print(f"\n{tier_value.upper()}")
        print("-" * 40)
        print(f"  Total requests:     {summary.total}")
        print(
            f"  Successful:         {summary.success_count} ({summary.success_count * 100 // summary.total}%)"
        )
        print(
            f"  Blocked:            {summary.blocked_count} ({summary.blocked_count * 100 // summary.total if summary.total > 0 else 0}%)"
        )
        print(
            f"  CAPTCHA detected:  {summary.captcha_count} ({summary.captcha_count * 100 // summary.total if summary.total > 0 else 0}%)"
        )
        print(f"  Avg duration:       {summary.avg_duration_ms:.0f}ms")
        print(f"  Total duration:     {summary.total_duration_ms}ms")


def save_results(
    summaries: dict[str, BenchmarkSummary],
    output_file: str = "benchmarks/results.json",
) -> None:
    """Save results to JSON file."""
    data: dict[str, Any] = {}

    for tier_value, summary in summaries.items():
        data[tier_value] = {
            "total": summary.total,
            "success_count": summary.success_count,
            "blocked_count": summary.blocked_count,
            "captcha_count": summary.captcha_count,
            "avg_duration_ms": summary.avg_duration_ms,
            "total_duration_ms": summary.total_duration_ms,
            "results": [
                {
                    "domain": r.domain,
                    "status_code": r.status_code,
                    "success": r.success,
                    "blocked": r.blocked,
                    "captcha_detected": r.captcha_detected,
                    "duration_ms": r.duration_ms,
                    "html_length": r.html_length,
                    "error": r.error,
                }
                for r in summary.results
            ],
        }

    with open(output_file, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\nResults saved to {output_file}")


async def main():
    """Main entry point."""
    domains = DEFAULT_TEST_DOMAINS

    if len(sys.argv) > 1:
        domains = sys.argv[1:]

    print(f"Running benchmarks on {len(domains)} domains...")
    print(f"Domains: {', '.join(domains)}")

    summaries = await run_benchmarks(domains)
    print_results(summaries)
    save_results(summaries)


if __name__ == "__main__":
    asyncio.run(main())
