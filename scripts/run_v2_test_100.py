#!/usr/bin/env python3
"""V2 method test: first 100 entries of companies_500.csv.

Full v2 flow: fetch -> TechParser -> Wappalyzer (pool) -> MX + SSL.
Output: companies_500_first100_v2.csv
"""
from __future__ import annotations

import asyncio
import csv
import os
import time
from concurrent.futures import ProcessPoolExecutor

from run_tech_detection_100k import (
    ENRICH_CONCURRENCY, FIELDS, build_row, clean_host, enrich_domain, fetch_batch,
)
from src.scraping.parser.tech_parser import TechParser
from src.scraping.parser.wappalyzer_runner import wapp_by_column, wapp_init, wapp_worker

LIMIT = int(os.environ.get("LIMIT", "100"))
INPUT_CSV = os.path.join(os.path.dirname(__file__), "..", "companies_500.csv")
OUTPUT_CSV = os.path.join(os.path.dirname(__file__), "..", "companies_500_first100_v2.csv")


def load_500() -> list[dict]:
    with open(INPUT_CSV, encoding="utf-8-sig") as f:
        rows = []
        for r in csv.DictReader(f):
            rows.append({
                "PARTY_NAME": r.get("Company_Name", ""),
                "WEB_ADDRESS": r.get("WEBSITE", ""),
                "COUNTRY": r.get("COUNTRY", ""),
            })
    return rows


def main() -> None:
    t0 = time.time()
    rows = load_500()[:LIMIT]
    results = asyncio.run(fetch_batch(rows))
    t_fetch = time.time() - t0

    pending = []
    deferred = []
    for src, (html, headers, error) in zip(rows, results):
        if error:
            deferred.append(build_row(src, "FETCH_ERROR", 0, error, {}))
        elif not html or len(html) < 200:
            deferred.append(build_row(src, "EMPTY", len(html or ""), "", {}))
        else:
            tech = TechParser(html, headers).detect()
            pending.append((src, tech, html, str(src.get("WEB_ADDRESS", "")).strip(), headers))

    tw = time.time()
    with ProcessPoolExecutor(max_workers=7, initializer=wapp_init) as pool:
        wapp_results = list(pool.map(wapp_worker, [(p[3], p[2], p[4]) for p in pending]))
    t_wapp = time.time() - tw

    te = time.time()
    async def run_enrich():
        sem = asyncio.Semaphore(ENRICH_CONCURRENCY)
        return await asyncio.gather(
            *[enrich_domain(sem, clean_host(p[3])) for p in pending])
    enrich = asyncio.run(run_enrich()) if pending else []
    t_enrich = time.time() - te

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for row in deferred:
            w.writerow(row)
        for (src, tech, html, _, _), full, (email, issuer, expiry) in \
                zip(pending, wapp_results, enrich):
            tech["_wapp_cols"] = wapp_by_column(full)
            tech["_wapp_flat"] = sorted(full)
            tech["_email"] = email
            tech["_ssl_issuer"] = issuer
            tech["_ssl_expiry"] = expiry
            w.writerow(build_row(src, "OK", len(html), "", tech))

    n = len(pending)
    print(f"rows: {len(rows)} | OK: {n} | FETCH_ERROR/EMPTY: {len(deferred)}")
    print(f"fetch: {t_fetch:.0f}s | wappalyzer: {t_wapp:.0f}s ({t_wapp/max(n,1)*1000:.0f}ms/page) | mx+ssl: {t_enrich:.0f}s")
    print(f"total: {time.time()-t0:.0f}s | Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
