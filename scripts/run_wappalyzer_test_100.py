#!/usr/bin/env python3
"""100-company test run: TechParser + Wappalyzer side by side.

Reuses fetch/load helpers from run_tech_detection_100k. Writes to its own CSV.
Reports per-page Wappalyzer overhead for 100k planning.
"""
from __future__ import annotations

import asyncio
import csv
import os
import time
from concurrent.futures import ProcessPoolExecutor

from run_tech_detection_100k import FIELDS, build_row, fetch_batch, load_rows
from src.scraping.parser.tech_parser import TechParser
from src.scraping.parser.wappalyzer_runner import wapp_init, wapp_worker

LIMIT = int(os.environ.get("LIMIT", "100"))
OUTPUT_CSV = os.path.join(os.path.dirname(__file__), "..", "companies_100_wapp_test.csv")


def main() -> None:
    t0 = time.time()
    rows = load_rows()[:LIMIT]
    results = asyncio.run(fetch_batch(rows))

    wapp_times: list[float] = []
    ok = 0
    pending = []  # (src, tech, html, domain, headers)
    deferred = []  # rows written as-is (errors/empty)
    for src, (html, headers, error) in zip(rows, results):
        domain = str(src.get("WEB_ADDRESS", "")).strip()
        if error:
            deferred.append(build_row(src, "FETCH_ERROR", 0, error, {}))
        elif not html or len(html) < 200:
            deferred.append(build_row(src, "EMPTY", len(html or ""), "", {}))
        else:
            tech = TechParser(html, headers).detect()
            pending.append((src, tech, html, domain, headers))
            ok += 1

    tw = time.time()
    tasks = [(p[3], p[2], p[4]) for p in pending]
    with ProcessPoolExecutor(max_workers=7, initializer=wapp_init) as pool:
        wapp_results = list(pool.map(wapp_worker, tasks))
    wapp_wall = time.time() - tw

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for row in deferred:
            w.writerow(row)
        for (src, tech, html, _, _), wapp in zip(pending, wapp_results):
            tech["_wappalyzer"] = wapp
            w.writerow(build_row(src, "OK", len(html), "", tech))

    n = len(pending)
    print(f"rows: {len(rows)} | OK: {ok} | fetch+parse: {time.time()-t0:.0f}s")
    if n:
        per_page = wapp_wall / n * 1000
        print(f"wappalyzer wall: {wapp_wall:.1f}s for {n} pages (7 workers) = {per_page:.0f}ms/page")
        print(f"100k estimate overhead: {per_page * 100_000 / 1000 / 3600:.1f}h (7 workers)")
    print(f"Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
