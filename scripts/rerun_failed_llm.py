#!/usr/bin/env python3
"""Rerun LLM fallback for rows whose earlier LLM call failed (llm_errors set).

Refetches homepage (HTML not stored), runs LLM, merges atomically.
"""
from __future__ import annotations

import asyncio
import csv
import os
import sys
import time

import httpx

from src.scraping.parser.tech_parser import TechParser
from src.services.llm_extractor import LLMExtractor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts.run_tech_detection_100k import (  # noqa: E402
    FIELDS, FETCH_HEADERS, build_row, llm_one, LLM_CONCURRENCY,
)

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "100k_tech_stacks.csv")
WORKERS = 150
TIMEOUT = 20


async def fetch_one(sem, client, domain):
    async with sem:
        url = domain if domain.startswith(("http://", "https://")) else f"https://{domain}"
        try:
            resp = await client.get(url, headers=FETCH_HEADERS, follow_redirects=True,
                                    timeout=TIMEOUT)
            return resp.text, ""
        except Exception as e:
            return "", repr(e)


async def main() -> None:
    t0 = time.time()
    with open(CSV_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    todo_idx = [i for i, r in enumerate(rows)
                if r.get("llm_call_made") == "True" and (r.get("llm_errors") or "").strip()]
    print(f"Rows with failed LLM: {len(todo_idx)}")
    if not todo_idx:
        return

    # Refetch HTML
    sem = asyncio.Semaphore(WORKERS)
    limits = httpx.Limits(max_connections=WORKERS + 50, max_keepalive_connections=WORKERS)
    html_map: dict[int, str] = {}
    done = 0
    async with httpx.AsyncClient(limits=limits) as client:
        for start in range(0, len(todo_idx), 1000):
            batch = todo_idx[start:start + 1000]
            results = await asyncio.gather(*[
                fetch_one(sem, client, rows[i]["WEB_ADDRESS"].strip()) for i in batch
            ])
            for i, (html, err) in zip(batch, results):
                if html and len(html) >= 200:
                    html_map[i] = html
            done += len(batch)
            print(f"[refetch] {done}/{len(todo_idx)} ({len(html_map)} usable)")

    # LLM pass — 10 concurrent, checkpoint-save every 100 completed rows
    print(f"\nLLM pass for {len(html_map)} rows...")
    lsem = asyncio.Semaphore(10)
    extractor = LLMExtractor()
    idxs = list(html_map)

    def save_csv():
        tmp = CSV_PATH + ".tmp"
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, CSV_PATH)

    async def process_one(i):
        llm_data = await llm_one(lsem, extractor, html_map[i])
        src = {"PARTY_NAME": rows[i]["PARTY_NAME"],
               "WEB_ADDRESS": rows[i]["WEB_ADDRESS"],
               "COUNTRY": rows[i]["COUNTRY"]}
        tech = TechParser(html_map[i], {}).detect()
        rows[i] = build_row(src, "OK", len(html_map[i]), "", tech, llm_data, llm_made=True)
        return not (llm_data.get("_extraction_errors") if isinstance(llm_data, dict) else ["?"])

    ok = fail = done = 0
    for start in range(0, len(idxs), 100):
        batch = idxs[start:start + 100]
        results = await asyncio.gather(*[process_one(i) for i in batch])
        for r in results:
            ok += r
            fail += not r
        done += len(batch)
        save_csv()  # checkpoint every 100
        print(f"[llm] {done}/{len(idxs)} ok={ok} fail={fail} "
              f"({done/(time.time()-t0):.1f}/s)")

    save_csv()
    print(f"Saved. Time: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
