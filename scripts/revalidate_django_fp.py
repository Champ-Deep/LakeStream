"""Re-validate Django false positives against deep.csv after signature fix."""
import asyncio
import csv

import httpx

from src.scraping.parser.tech_parser import TechParser

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def load(path, domain_col):
    rows = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows[r[domain_col].strip().lower().removeprefix("www.")] = r
    return rows


def names_from_detections(detections_str):
    return {
        p.strip().split(" (")[0].strip().lower()
        for p in (detections_str or "").split(";")
        if p.strip()
    }


ours = load("companies_500_tech_stacks.csv", "WEBSITE")
deep = load("deep.csv", "domain")

fp_domains = [
    d for d, r in ours.items()
    if "django" in names_from_detections(r["detections"])
]
print(f"Re-validating {len(fp_domains)} domains previously flagged Django")


async def fetch(client, domain):
    try:
        resp = await client.get(f"https://{domain}", headers=HEADERS,
                                follow_redirects=True, timeout=30)
        return resp.text, {str(k): str(v) for k, v in resp.headers.items()}, ""
    except Exception as e:
        return "", {}, repr(e)


async def main():
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[fetch(client, d) for d in fp_domains])

    fixed = still_fp = fetch_err = 0
    for domain, (html, headers, error) in zip(fp_domains, results):
        deep_names = names_from_detections(deep.get(domain, {}).get("detections", ""))
        if error:
            fetch_err += 1
            print(f"{domain:45s} FETCH_ERROR  deep={sorted(deep_names)}")
            continue
        tech = TechParser(html, headers).detect()
        new_names = {d["name"].lower() for d in tech.get("detections", [])}
        if "django" in new_names:
            still_fp += 1
            verdict = "STILL_DJANGO"
        else:
            fixed += 1
            verdict = "FIXED"
        inter = new_names & deep_names
        union = new_names | deep_names
        j = len(inter) / len(union) if union else 1.0
        print(f"{domain:45s} {verdict:13s} jaccard_vs_deep={j:.2f} ours={sorted(new_names)} deep={sorted(deep_names)}")

    print(f"\nfixed={fixed} still_django={still_fp} fetch_errors={fetch_err}")


asyncio.run(main())
