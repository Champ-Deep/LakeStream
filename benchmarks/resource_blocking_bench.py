"""Bytes per page with and without Playwright resource blocking.

Loads each URL the same way LakePlaywrightFetcher does (fresh context,
goto, wait for networkidle up to 10 s, page.content()), once with blocking
off and once with it on, and records:

* network bytes received (Chrome DevTools ``Network.loadingFinished`` /
  ``encodedDataLength``: headers + body as they came over the wire)
* request count, blocked count by reason, wall time
* what pricing_parser and contact_parser extract, to show blocking does not
  change the structured output

The run order alternates per URL (off-first, then on-first) so CDN warm-up does
not favour one mode. Only numbers are written; no scraped HTML is stored.

    python benchmarks/resource_blocking_bench.py   # the 20 URLs in resource_blocking_urls.txt
    python benchmarks/resource_blocking_bench.py --out data/bench --urls my_urls.txt
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.scraping.fetcher.resource_blocking import install_resource_blocking  # noqa: E402
from src.scraping.parser.contact_parser import ContactParser  # noqa: E402
from src.scraping.parser.pricing_parser import PricingParser  # noqa: E402

DEFAULT_URLS = Path(__file__).with_name("resource_blocking_urls.txt")
GOTO_TIMEOUT_MS = 30_000
IDLE_TIMEOUT_MS = 10_000  # same as the fetcher


def read_urls(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


async def load_once(browser, url: str, block: bool) -> dict:
    context = await browser.new_context()
    page = await context.new_page()
    cdp = await context.new_cdp_session(page)
    await cdp.send("Network.enable")
    received = {"bytes": 0, "finished": 0, "failed": 0}

    def on_finished(ev):
        received["bytes"] += int(ev.get("encodedDataLength", 0))
        received["finished"] += 1

    def on_failed(_ev):
        received["failed"] += 1

    cdp.on("Network.loadingFinished", on_finished)
    cdp.on("Network.loadingFailed", on_failed)

    stats = await install_resource_blocking(page) if block else None
    start = time.time()
    status, error, html = 0, "", ""
    try:
        resp = await page.goto(url, timeout=GOTO_TIMEOUT_MS)
        status = resp.status if resp else 0
        try:
            await page.wait_for_load_state("networkidle", timeout=IDLE_TIMEOUT_MS)
        except Exception:
            pass
        html = await page.content()
    except Exception as exc:  # timeouts, DNS, etc.
        error = f"{type(exc).__name__}: {str(exc)[:120]}"
    elapsed = time.time() - start
    await asyncio.sleep(0.3)  # let late loadingFinished events land
    await context.close()

    plans = PricingParser(html, url).extract_pricing_plans() if html else []
    people = ContactParser(html, url).extract_people() if html else []
    return {
        "status": status,
        "error": error,
        "bytes": received["bytes"],
        "requests_finished": received["finished"],
        "requests_failed": received["failed"],
        "blocked": stats.blocked if stats else 0,
        "blocked_by_reason": dict(stats.by_reason) if stats else {},
        "seconds": round(elapsed, 2),
        "html_chars": len(html),
        "plans": sorted(p.get("plan_name") or "" for p in plans),
        "people": sorted(f"{p.get('first_name')} {p.get('last_name')}" for p in people),
    }


async def run(urls: list[str], out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for i, url in enumerate(urls, start=1):
            order = (False, True) if i % 2 else (True, False)
            res = {}
            for block in order:
                res[block] = await load_once(browser, url, block)
            off, on = res[False], res[True]
            saved = off["bytes"] - on["bytes"]
            row = {
                "n": i,
                "url": url,
                "status_off": off["status"],
                "status_on": on["status"],
                "bytes_off": off["bytes"],
                "bytes_on": on["bytes"],
                "bytes_saved": saved,
                "saved_pct": round(100 * saved / off["bytes"], 1) if off["bytes"] else 0.0,
                "requests_off": off["requests_finished"],
                "requests_on": on["requests_finished"],
                "blocked_on": on["blocked"],
                "blocked_by_reason": json.dumps(on["blocked_by_reason"], sort_keys=True),
                "seconds_off": off["seconds"],
                "seconds_on": on["seconds"],
                "plans_off": len(off["plans"]),
                "plans_on": len(on["plans"]),
                "people_off": len(off["people"]),
                "people_on": len(on["people"]),
                "extraction_same": off["plans"] == on["plans"] and off["people"] == on["people"],
                "error": off["error"] or on["error"],
            }
            rows.append(row)
            mb_off, mb_on = off["bytes"] / 1e6, on["bytes"] / 1e6
            print(
                f"{i:2d}. {url[:52]:52} off {mb_off:6.2f} MB  on {mb_on:6.2f} MB"
                f"  saved {row['saved_pct']:5.1f}%  same={row['extraction_same']}"
                + (f"  ERR {row['error'][:40]}" if row["error"] else ""),
                flush=True,
            )
            # write as we go so a killed run keeps its numbers
            csv_path = out_dir / "resource_blocking_bytes.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        await browser.close()
    return rows


def summarize(rows: list[dict]) -> dict:
    # only pages that actually loaded both times; a bot block (403 etc.) is an
    # escalation case, not a blocking measurement
    ok = [
        r
        for r in rows
        if not r["error"] and r["status_off"] == 200 and r["status_on"] == 200 and r["bytes_off"]
    ]
    tot_off = sum(r["bytes_off"] for r in ok)
    tot_on = sum(r["bytes_on"] for r in ok)
    return {
        "urls": len(rows),
        "measured": len(ok),
        "excluded": [f"{r['url']} (status {r['status_off']})" for r in rows if r not in ok],
        "total_mb_off": round(tot_off / 1e6, 2),
        "total_mb_on": round(tot_on / 1e6, 2),
        "total_saved_pct": round(100 * (tot_off - tot_on) / tot_off, 1) if tot_off else 0.0,
        "mean_mb_per_page_off": round(tot_off / len(ok) / 1e6, 2) if ok else 0.0,
        "mean_mb_per_page_on": round(tot_on / len(ok) / 1e6, 2) if ok else 0.0,
        "median_saved_pct": statistics.median(r["saved_pct"] for r in ok) if ok else 0.0,
        "median_seconds_off": statistics.median(r["seconds_off"] for r in ok) if ok else 0.0,
        "median_seconds_on": statistics.median(r["seconds_on"] for r in ok) if ok else 0.0,
        "extraction_same": sum(1 for r in ok if r["extraction_same"]),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--urls", type=Path, default=DEFAULT_URLS)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "bench")
    a = ap.parse_args()
    rows = asyncio.run(run(read_urls(a.urls), a.out))
    summary = summarize(rows)
    summary_path = a.out / "resource_blocking_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
