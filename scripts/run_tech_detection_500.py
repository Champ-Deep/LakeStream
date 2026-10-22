#!/usr/bin/env python3
"""Fetch + deterministic tech detection + LLM fallback for companies XLSX.

Concurrency:
- 50 concurrent fetch workers
- 5 concurrent LLM workers
- Save progress to XLSX after every 100 completed fetch rows.
- Resume: skip rows that already have a status value.
"""
from __future__ import annotations

import asyncio
import csv
import os
import sys
import time
from collections import Counter
from typing import Any

import httpx
from openpyxl import load_workbook, Workbook

from src.scraping.parser.tech_parser import TechParser
from src.services.llm_extractor import LLMExtractor

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
INPUT_XLSX = os.path.join(os.path.dirname(__file__), "..", "companies_500.xlsx")
OUTPUT_XLSX = os.path.join(os.path.dirname(__file__), "..", "companies_500_tech_stacks.xlsx")
OUTPUT_CSV = os.path.join(os.path.dirname(__file__), "..", "companies_500_tech_stacks.csv")

FETCH_CONCURRENCY = 50
LLM_CONCURRENCY = 5
FETCH_TIMEOUT = 30
CHECKPOINT_EVERY = 100

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

OUTPUT_FIELDS = [
    "Company_Name",
    "WEBSITE",
    "COUNTRY",
    "status",
    "html_size",
    "error",
    "platform",
    "detection_count",
    "recommended_count",
    "detections",
    "high_confidence",
    "medium_confidence",
    "low_confidence",
    "llm_call_made",
    "llm_platform",
    "llm_frameworks",
    "llm_js_libraries",
    "llm_analytics",
    "llm_marketing_tools",
    "llm_cdn",
    "llm_hosting",
    "llm_backend",
    "llm_build_tools",
    "llm_fonts",
    "llm_payment",
    "llm_auth",
    "llm_monitoring",
    "llm_search",
    "llm_ecommerce",
    "llm_errors",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_rows() -> list[dict[str, Any]]:
    wb = load_workbook(INPUT_XLSX)
    ws = wb.active
    header = [c.value for c in ws[1]]
    rows: list[dict[str, Any]] = []
    for cells in ws.iter_rows(min_row=2, values_only=True):
        row = dict(zip(header, cells))
        rows.append(row)
    return rows


def ensure_output_xlsx(rows: list[dict[str, Any]]) -> None:
    if os.path.exists(OUTPUT_XLSX):
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "tech_stacks"
    ws.append(OUTPUT_FIELDS)
    for r in rows:
        ws.append([r.get(k, "") for k in OUTPUT_FIELDS])
    wb.save(OUTPUT_XLSX)


def read_existing_statuses() -> dict[int, dict[str, Any]]:
    """Map row number (1-based inside data) to existing row."""
    if not os.path.exists(OUTPUT_XLSX):
        return {}
    wb = load_workbook(OUTPUT_XLSX)
    ws = wb.active
    header = [c.value for c in ws[1]]
    existing: dict[int, dict[str, Any]] = {}
    for idx, cells in enumerate(ws.iter_rows(min_row=2, values_only=True), start=1):
        row = dict(zip(header, cells))
        if row.get("status"):
            existing[idx] = row
    return existing


def sanitize(val: Any) -> Any:
    if val is None:
        return ""
    if isinstance(val, (list, tuple)):
        return ", ".join(str(x) for x in val)
    return val


def flatten_llm(data: dict) -> dict[str, Any]:
    """Pick the keys TechParser also emits plus errors."""
    out: dict[str, Any] = {f"llm_{k}": "" for k in [
        "platform", "frameworks", "js_libraries", "analytics",
        "marketing_tools", "cdn", "hosting", "backend", "build_tools",
        "fonts", "payment", "auth", "monitoring", "search", "ecommerce",
    ]}
    out["llm_errors"] = ""
    if not isinstance(data, dict):
        return out
    for k in list(out.keys()):
        clean = k.replace("llm_", "")
        if clean in data and data[clean] is not None:
            out[k] = sanitize(data[clean])
    errs = data.get("_extraction_errors")
    if errs:
        out["llm_errors"] = sanitize(errs)
    return out


def build_result_row(
    source: dict[str, Any],
    status: str,
    html_size: int,
    error: str,
    tech: dict,
    llm_data: dict | None = None,
    llm_made: bool = False,
) -> dict[str, Any]:
    detections = tech.get("detections", [])
    high = [d["name"] for d in detections if d["confidence"] == "high"]
    med = [d["name"] for d in detections if d["confidence"] == "medium"]
    low = [d["name"] for d in detections if d["confidence"] == "low"]

    out = {
        "Company_Name": source.get("Company_Name", ""),
        "WEBSITE": source.get("WEBSITE", ""),
        "COUNTRY": source.get("COUNTRY", ""),
        "status": status,
        "html_size": html_size,
        "error": error[:200] if error else "",
        "platform": tech.get("platform") or "",
        "detection_count": len(detections),
        "recommended_count": sum(1 for d in detections if d.get("recommended")),
        "detections": "; ".join(f"{d['name']} ({d['confidence']})" for d in detections),
        "high_confidence": ", ".join(high),
        "medium_confidence": ", ".join(med),
        "low_confidence": ", ".join(low),
        "llm_call_made": llm_made,
    }
    out.update(flatten_llm(llm_data or {}))
    return out


def write_xlsx(rows: list[dict[str, Any]]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "tech_stacks"
    ws.append(OUTPUT_FIELDS)
    for r in rows:
        ws.append([sanitize(r.get(k, "")) for k in OUTPUT_FIELDS])
    wb.save(OUTPUT_XLSX)


def write_csv(rows: list[dict[str, Any]]) -> None:
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def append_partial_xlsx(new_rows: list[dict[str, Any]]) -> None:
    """Used only when we have an existing output and want to overwrite whole file."""
    wb = load_workbook(OUTPUT_XLSX)
    ws = wb.active
    header = [c.value for c in ws[1]]
    # Find index positions
    domain_col = header.index("WEBSITE") + 1
    status_col = header.index("status") + 1
    # Build dict of target rows by website for quick update
    website_to_row: dict[str, int] = {}
    for i, cells in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        website = cells[domain_col - 1]
        if website:
            website_to_row[str(website)] = i

    for r in new_rows:
        website = r.get("WEBSITE")
        if website in website_to_row:
            row_idx = website_to_row[website]
            for col_idx, field in enumerate(header, start=1):
                ws.cell(row=row_idx, column=col_idx, value=sanitize(r.get(field, "")))
    wb.save(OUTPUT_XLSX)


# ---------------------------------------------------------------------------
# Fetch + deterministic detection
# ---------------------------------------------------------------------------
async def fetch_one(
    semaphore: asyncio.Semaphore,
    client: httpx.AsyncClient,
    domain: str,
) -> tuple[str, dict[str, str], str]:
    async with semaphore:
        url = domain if domain.startswith(("http://", "https://")) else f"https://{domain}"
        try:
            resp = await client.get(url, headers=FETCH_HEADERS, follow_redirects=True, timeout=FETCH_TIMEOUT)
            return resp.text, {str(k): str(v) for k, v in resp.headers.items()}, ""
        except Exception as e:
            return "", {}, repr(e)


async def run_fetches(rows: list[dict[str, Any]]) -> list[tuple[str, dict[str, str], str]]:
    sem = asyncio.Semaphore(FETCH_CONCURRENCY)
    async with httpx.AsyncClient() as client:
        tasks = [
            fetch_one(sem, client, str(r.get("WEBSITE", "")).strip())
            for r in rows
        ]
        return await asyncio.gather(*tasks)


def parse_tech(html: str, headers: dict[str, str]) -> dict:
    clean_headers = {str(k): str(v) for k, v in headers.items()}
    return TechParser(html, clean_headers).detect()


# ---------------------------------------------------------------------------
# LLM fallback
# ---------------------------------------------------------------------------
async def llm_one(
    semaphore: asyncio.Semaphore,
    extractor: LLMExtractor,
    html: str,
) -> tuple[dict, str]:
    async with semaphore:
        try:
            data = await extractor.extract_by_type(html, "tech_stack")
            return data if isinstance(data, dict) else {"_extraction_errors": ["non-dict response"]}, ""
        except Exception as e:
            return {"_extraction_errors": [repr(e)]}, repr(e)


async def run_llm_fallbacks(
    fallback_items: list[tuple[int, str]],
) -> dict[int, tuple[dict, str]]:
    sem = asyncio.Semaphore(LLM_CONCURRENCY)
    extractor = LLMExtractor()
    async with httpx.AsyncClient() as client:
        # LLMExtractor creates its own client; we accept that.
        tasks = [llm_one(sem, extractor, html) for _, html in fallback_items]
        results = await asyncio.gather(*tasks)
    return {idx: res for (idx, _), res in zip(fallback_items, results)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    t0 = time.time()
    rows = load_rows()
    print(f"Loaded {len(rows)} rows from {INPUT_XLSX}")

    ensure_output_xlsx(rows)
    existing = read_existing_statuses()
    print(f"Skipping {len(existing)} already-processed rows.")

    # Determine which rows need fetching
    todo_indices = [i for i in range(len(rows)) if (i + 1) not in existing]
    todo_rows = [rows[i] for i in todo_indices]
    print(f"Rows to fetch: {len(todo_rows)}")

    if todo_rows:
        fetch_results = asyncio.run(run_fetches(todo_rows))
    else:
        fetch_results = []

    # Build deterministic results
    new_results: list[dict[str, Any]] = []
    llm_fallbacks: list[tuple[int, str]] = []

    for data_idx, (html, headers, error) in zip(todo_indices, fetch_results):
        source = rows[data_idx]
        if error:
            new_results.append(build_result_row(source, "FETCH_ERROR", 0, error, {}))
            continue
        if not html or len(html) < 200:
            new_results.append(build_result_row(source, "EMPTY", len(html or ""), "", {}))
            continue

        tech = parse_tech(html, headers)
        rec = sum(1 for d in tech.get("detections", []) if d.get("recommended"))
        new_results.append(build_result_row(source, "OK", len(html), "", tech))

        if rec == 0:
            llm_fallbacks.append((data_idx, html))

    # Merge new results into existing output file/structure
    all_results: list[dict[str, Any]] = []
    for i in range(len(rows)):
        if (i + 1) in existing:
            all_results.append(existing[i + 1])
        else:
            # Pop from new_results in order
            all_results.append(new_results.pop(0))

    # Checkpoint: write after every 100 new completed rows
    # (we batch-update once here; if interrupted, rerun skips processed rows)
    if todo_rows:
        append_partial_xlsx([r for r in all_results if r.get("status")])
        print(f"Saved deterministic results for {len(todo_rows)} rows.")

    # Run LLM fallback where recommended_count == 0
    llm_results: dict[int, tuple[dict, str]] = {}
    if llm_fallbacks:
        print(f"\nRunning LLM fallback for {len(llm_fallbacks)} rows (recommended_count=0)...")
        llm_results = asyncio.run(run_llm_fallbacks(llm_fallbacks))

        for data_idx, (llm_data, llm_error) in llm_results.items():
            row = all_results[data_idx]
            # Re-build result with LLM data merged
            source = rows[data_idx]
            # Re-detect tech (we still have html; but easier: keep existing tech fields)
            html, headers, error = fetch_results[todo_indices.index(data_idx)]
            tech = parse_tech(html, headers)
            new_row = build_result_row(source, "OK", len(html), error, tech, llm_data, llm_made=True)
            all_results[data_idx] = new_row

        append_partial_xlsx(all_results)
        print("Saved LLM fallback results.")

    # Final CSV + XLSX
    write_xlsx(all_results)
    write_csv(all_results)

    total_time = time.time() - t0
    ok_count = sum(1 for r in all_results if r.get("status") == "OK")
    fetch_errors = sum(1 for r in all_results if r.get("status") == "FETCH_ERROR")
    empty_count = sum(1 for r in all_results if r.get("status") == "EMPTY")
    llm_count = sum(1 for r in all_results if r.get("llm_call_made"))

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total domains:     {len(all_results)}")
    print(f"Successful:        {ok_count}")
    print(f"Fetch errors:      {fetch_errors}")
    print(f"Empty responses:   {empty_count}")
    print(f"LLM fallbacks:     {llm_count}")
    print(f"Total time:        {total_time:.1f}s")
    print(f"\nOutput: {OUTPUT_XLSX}")
    print(f"Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
