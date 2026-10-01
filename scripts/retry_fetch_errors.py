#!/usr/bin/env python3
"""Retry FETCH_ERROR rows from 100k_tech_stacks.csv.

- Skips DNS-resolution failures (dead domains)
- 100 workers, 15s connect / 60s read timeout
- Success -> OK via TechParser (rec=0 queued for LLM pass)
- Still failing -> DEAD
- Atomic CSV rewrite
"""
from __future__ import annotations

import asyncio
import csv
import os
import re
import sys
import time
from typing import Any

import httpx

from src.scraping.parser.tech_parser import TechParser

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts.run_tech_detection_100k import (  # noqa: E402
    FIELDS, FETCH_HEADERS, build_row, flatten_llm, llm_one, LLM_CONCURRENCY,
)
from src.services.llm_extractor import LLMExtractor  # noqa: E402

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "100k_tech_stacks.csv")

WORKERS = 150
TIMEOUT = httpx.Timeout(60.0, connect=15.0)
DNS_PAT = re.compile(r"getaddrinfo|Name or service|nodename|DNS", re.I)
PROGRESS_EVERY = 1000


async def fetch_one(sem: asyncio.Semaphore, client: httpx.AsyncClient,
                    domain: str) -> tuple[str, dict[str, str], str]:
    async with sem:
        url = domain if domain.startswith(("http://", "https://")) else f"https://{domain}"
        try:
            resp = await client.get(url, headers=FETCH_HEADERS, follow_redirects=True,
                                    timeout=TIMEOUT)
            return resp.text, {str(k): str(v) for k, v in resp.headers.items()}, ""
        except Exception as e:
            return "", {}, repr(e)


async def main() -> None:
    t0 = time.time()
    with open(CSV_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    retry_idx = [
        i for i, r in enumerate(rows)
        if r["status"] == "FETCH_ERROR" and not DNS_PAT.search(r.get("error") or "")
    ]
    print(f"Total rows: {len(rows)} | Retrying: {len(retry_idx)} (DNS failures skipped)")

    sem = asyncio.Semaphore(WORKERS)
    limits = httpx.Limits(max_connections=WORKERS + 50, max_keepalive_connections=WORKERS)

    recovered: list[tuple[int, str]] = []  # (row_idx, html) for LLM queue
    done = 0
    async with httpx.AsyncClient(limits=limits) as client:
        for start in range(0, len(retry_idx), PROGRESS_EVERY):
            batch_i = retry_idx[start:start + PROGRESS_EVERY]
            results = await asyncio.gather(*[
                fetch_one(sem, client, rows[i]["WEB_ADDRESS"].strip()) for i in batch_i
            ])
            for i, (html, headers, error) in zip(batch_i, results):
                src = {"PARTY_NAME": rows[i]["PARTY_NAME"],
                       "WEB_ADDRESS": rows[i]["WEB_ADDRESS"],
                       "COUNTRY": rows[i]["COUNTRY"]}
                if error:
                    rows[i] = build_row(src, "DEAD", 0, error, {})
                elif not html or len(html) < 200:
                    rows[i] = build_row(src, "EMPTY", len(html or ""), "", {})
                else:
                    tech = TechParser(html, headers).detect()
                    rows[i] = build_row(src, "OK", len(html), "", tech)
                    if sum(1 for d in tech.get("detections", []) if d.get("recommended")) == 0:
                        recovered.append((i, html))
            done += len(batch_i)
            print(f"[retry] {done}/{len(retry_idx)} "
                  f"({done/(time.time()-t0):.1f}/s, elapsed {time.time()-t0:.0f}s)")

    # Write fetch results BEFORE LLM pass (abort-safe)
    def write_csv():
        tmp = CSV_PATH + ".tmp"
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, CSV_PATH)

    write_csv()
    print("Fetch results saved.")

    # LLM pass on recovered zero-detection rows
    if recovered:
        print(f"\nLLM fallback for {len(recovered)} recovered rows...")
        lsem = asyncio.Semaphore(LLM_CONCURRENCY)
        extractor = LLMExtractor()
        llm_results = await asyncio.gather(*[llm_one(lsem, extractor, html)
                                             for _, html in recovered])
        for (i, html), llm_data in zip(recovered, llm_results):
            src = {"PARTY_NAME": rows[i]["PARTY_NAME"],
                   "WEB_ADDRESS": rows[i]["WEB_ADDRESS"],
                   "COUNTRY": rows[i]["COUNTRY"]}
            tech = TechParser(html, {}).detect()
            rows[i] = build_row(src, "OK", len(html), "", tech, llm_data, llm_made=True)
        write_csv()
        print("LLM results saved.")

    from collections import Counter
    c = Counter(r["status"] for r in rows)
    print("\n" + "=" * 60)
    print(f"Final: {dict(c)}")
    print(f"Time: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
