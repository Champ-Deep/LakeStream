#!/usr/bin/env python3
"""Test tech stack detection on domains from an Excel spreadsheet."""
import argparse
import asyncio
import csv
import time
from collections import Counter

import httpx
import openpyxl
from src.scraping.parser.tech_parser import TechParser

DEFAULT_XLSX = "/Users/deep/Downloads/100-200 Test.xlsx"

FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


async def fetch(semaphore: asyncio.Semaphore, client: httpx.AsyncClient, domain: str, timeout: int):
    async with semaphore:
        url = domain if domain.startswith(("http://", "https://")) else f"https://{domain}"
        try:
            resp = await client.get(url, headers=FETCH_HEADERS, follow_redirects=True, timeout=timeout)
            return resp.text, dict(resp.headers), None
        except Exception as e:
            return "", {}, repr(e)


def parse_tech(html: str, resp_headers: dict) -> dict:
    clean_headers = {str(k): str(v) for k, v in resp_headers.items()}
    return TechParser(html, clean_headers).detect()


def main():
    parser = argparse.ArgumentParser(description="Run tech stack detection on domains from XLSX")
    parser.add_argument("-i", "--input", default=DEFAULT_XLSX, help="Input XLSX file path")
    parser.add_argument("-o", "--output", help="Output CSV file path")
    parser.add_argument("-c", "--concurrent", type=int, default=8)
    parser.add_argument("-t", "--timeout", type=int, default=20)
    args = parser.parse_args()

    output_csv = args.output or args.input.replace(".xlsx", "_results.csv")

    wb = openpyxl.load_workbook(args.input)
    ws = wb.active

    rows = []
    for r in range(2, ws.max_row + 1):
        name = ws.cell(r, 1).value
        domain = ws.cell(r, 2).value
        country = ws.cell(r, 3).value
        if domain:
            rows.append((name, str(domain).strip(), str(country).strip() if country else ""))

    print(f"Total domains: {len(rows)}")
    print(f"Concurrency: {args.concurrent} | Timeout: {args.timeout}s")
    print(f"Output CSV: {output_csv}\n")

    results = []

    async def run_all():
        sem = asyncio.Semaphore(args.concurrent)
        async with httpx.AsyncClient() as client:
            tasks = [fetch(sem, client, domain, args.timeout) for _, domain, _ in rows]
            return await asyncio.gather(*tasks)

    t0 = time.time()
    fetch_results = asyncio.run(run_all())
    total_time = time.time() - t0

    for (company, domain, country), (html, resp_headers, error) in zip(rows, fetch_results):
        if error:
            results.append({
                "company": company, "domain": domain, "country": country,
                "status": "FETCH_ERROR", "html_size": 0, "error": error[:120],
                "platform": "", "detection_count": 0, "recommended_count": 0,
                "detections": "", "high_confidence": "", "medium_confidence": "", "low_confidence": "",
            })
            continue

        if not html or len(html) < 200:
            results.append({
                "company": company, "domain": domain, "country": country,
                "status": "EMPTY", "html_size": len(html), "error": "",
                "platform": "", "detection_count": 0, "recommended_count": 0,
                "detections": "", "high_confidence": "", "medium_confidence": "", "low_confidence": "",
            })
            continue

        tech_result = parse_tech(html, resp_headers)
        detections = tech_result.get("detections", [])
        high = [d["name"] for d in detections if d["confidence"] == "high"]
        med = [d["name"] for d in detections if d["confidence"] == "medium"]
        low = [d["name"] for d in detections if d["confidence"] == "low"]
        recommended = [d["name"] for d in detections if d.get("recommended")]

        results.append({
            "company": company, "domain": domain, "country": country,
            "status": "OK", "html_size": len(html), "error": "",
            "platform": tech_result.get("platform") or "",
            "detection_count": len(detections),
            "recommended_count": len(recommended),
            "detections": "; ".join(f"{d['name']} ({d['confidence']})" for d in detections),
            "high_confidence": ", ".join(high),
            "medium_confidence": ", ".join(med),
            "low_confidence": ", ".join(low),
        })

    # Write CSV
    fieldnames = ["company", "domain", "country", "status", "html_size", "error",
                  "platform", "detection_count", "recommended_count",
                  "detections", "high_confidence", "medium_confidence", "low_confidence"]
    with open(output_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    # Summary
    ok_count = sum(1 for r in results if r["status"] == "OK")
    fetch_errors = sum(1 for r in results if r["status"] == "FETCH_ERROR")
    empty_count = sum(1 for r in results if r["status"] == "EMPTY")
    with_platform = sum(1 for r in results if r["platform"])
    total_recommended = sum(r["recommended_count"] for r in results)

    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total domains:     {len(results)}")
    print(f"Successful:        {ok_count}")
    print(f"Fetch errors:      {fetch_errors}")
    print(f"Empty responses:   {empty_count}")
    print(f"Platform detected: {with_platform}/{ok_count}")
    print(f"Total detections:  {sum(r['detection_count'] for r in results)}")
    print(f"Recommended (H+M): {total_recommended}")
    print(f"Total time:        {total_time:.1f}s (avg {total_time/len(results):.1f}s/domain)")
    print()

    ok_results = [r for r in results if r["status"] == "OK"]

    # Confidence distribution
    high_count = sum(len(r["high_confidence"].split(", ")) for r in ok_results if r["high_confidence"])
    med_count = sum(len(r["medium_confidence"].split(", ")) for r in ok_results if r["medium_confidence"])
    low_count = sum(len(r["low_confidence"].split(", ")) for r in ok_results if r["low_confidence"])

    print(f"Confidence breakdown:")
    print(f"  HIGH:   {high_count} ({high_count/(high_count+med_count+low_count)*100:.0f}%)")
    print(f"  MEDIUM: {med_count} ({med_count/(high_count+med_count+low_count)*100:.0f}%)")
    print(f"  LOW:    {low_count} ({low_count/(high_count+med_count+low_count)*100:.0f}%)")
    print()

    # Top technologies
    all_dets = []
    for r in ok_results:
        all_dets.extend(d.strip() for d in r["detections"].split(";") if d.strip())
    name_counts = Counter(d.rsplit(" (", 1)[0] for d in all_dets)
    print("Top 15 detected technologies:")
    for name, count in name_counts.most_common(15):
        print(f"  {name:<30} {count:>3} sites")
    print()

    # Platform breakdown
    plat_counts = Counter(r["platform"] for r in results if r["platform"])
    print("Platforms detected:")
    for name, count in plat_counts.most_common():
        print(f"  {name:<30} {count:>3} sites")
    print()

    # Fetch errors detail
    if fetch_errors:
        print("Fetch errors:")
        for r in results:
            if r["status"] == "FETCH_ERROR":
                print(f"  {r['domain']:<40} {r['error']}")
        print()

    print(f"Results saved to: {output_csv}")


if __name__ == "__main__":
    main()
