#!/usr/bin/env python3
"""Simple entry point for the tech-detection pipeline.

Reads a list of companies/websites from a CSV or Excel file, fetches each
site, and detects its technology stack using the curated TechParser plus
Wappalyzer's ~3999-fingerprint database. Also looks up email hosting (MX)
and SSL certificate issuer/expiry for each domain.

Usage:
    python run_tech_detection.py path/to/companies.csv
    python run_tech_detection.py path/to/companies.xlsx
    python run_tech_detection.py            # you'll be prompted for a path

Input file requirements:
    Any CSV or .xlsx with a header row containing at least a website/domain
    column (e.g. "website", "domain", "url", "web_address"). A company-name
    column (e.g. "name", "company", "party_name") and a "country" column are
    picked up automatically if present, but are optional.

Output:
    Writes "<input_name>_tech_stack.csv" next to the input file.
"""
from __future__ import annotations

import asyncio
import csv
import os
import socket
import ssl
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Any

import dns.resolver
import httpx
import openpyxl

from src.scraping.parser.tech_parser import TechParser
from src.scraping.parser.wappalyzer_runner import wapp_by_column, wapp_init, wapp_worker

FETCH_CONCURRENCY = 20
FETCH_TIMEOUT = 20
ENRICH_CONCURRENCY = 20
WAPP_WORKERS = min(7, os.cpu_count() or 1)

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

TECH_COLUMNS = [
    "platform", "frameworks", "js_libraries", "analytics", "cdn",
    "hosting", "web_servers", "os", "programming_languages", "widgets",
]

# curated TechParser category -> output column (categories not listed have no curated source)
CURATED_CATEGORY_MAP = {
    "cms": "platform",
    "ecommerce": "platform",
    "framework": "frameworks",
    "js_library": "js_libraries",
    "analytics": "analytics",
    "cdn": "cdn",
    "hosting": "hosting",
}
_BACKEND_WEB_SERVERS = {"nginx", "apache", "envoy"}
_BACKEND_LANGUAGES = {"php", "asp.net", "java/spring"}

MX_PROVIDERS = [
    ("google.com", "Google Workspace"), ("googlemail.com", "Google Workspace"),
    ("outlook.com", "Microsoft 365"), ("microsoft.com", "Microsoft 365"),
    ("zoho", "Zoho Mail"), ("mimecast", "Mimecast"),
    ("proofpoint", "Proofpoint"), ("pphosted", "Proofpoint"),
    ("messagelabs", "Broadcom Email Security"), ("barracuda", "Barracuda"),
    ("amazonses", "Amazon SES"), ("mailgun", "Mailgun"), ("sendgrid", "SendGrid"),
    ("secureserver", "GoDaddy"), ("ionos", "IONOS"), ("one.com", "One.com"),
    ("ovh", "OVH"), ("infomaniak", "Infomaniak"), ("proton", "Proton Mail"),
    ("fastmail", "Fastmail"), ("yandex", "Yandex"),
]

NAME_HEADERS = {"name", "company", "company_name", "party_name", "organization"}
WEBSITE_HEADERS = {"website", "web_address", "domain", "url", "site"}
COUNTRY_HEADERS = {"country"}

FIELDS = [
    "company", "website", "country", "status", "html_size", "error",
    *TECH_COLUMNS, "email_hosting", "ssl_issuer", "ssl_expiry",
    "detection_count", "recommended_count", "detections",
    "high_confidence", "medium_confidence", "low_confidence", "wappalyzer_techs",
]


def curated_column(detection: dict) -> str | None:
    cat = detection.get("category")
    if cat == "backend":
        name = detection["name"].lower()
        if name in _BACKEND_WEB_SERVERS:
            return "web_servers"
        if name in _BACKEND_LANGUAGES:
            return "programming_languages"
        return "frameworks"
    return CURATED_CATEGORY_MAP.get(cat)


def merge_column(curated: list[tuple[str, str]], wapp_names: list[str]) -> str:
    seen = {n.lower() for n, _ in curated}
    parts = [f"{n} ({c})" for n, c in curated]
    parts.extend(n for n in wapp_names if n.lower() not in seen)
    return "|".join(parts)


def clean_host(domain: str) -> str:
    host = domain.strip()
    for scheme in ("https://", "http://"):
        if host.startswith(scheme):
            host = host[len(scheme):]
    host = host.split("/")[0].split(":")[0].strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host.lower()


def url_variants(domain: str) -> list[str]:
    host = clean_host(domain)
    return [f"https://{host}", f"https://www.{host}", f"http://{host}", f"http://www.{host}"]


def mx_provider(host: str) -> str:
    """Email hosting provider from MX records. Never raises."""
    if not host:
        return ""
    try:
        answers = dns.resolver.resolve(host, "MX", lifetime=5)
        hosts = sorted({str(r.exchange).lower().rstrip(".") for r in answers})
        blob = " ".join(hosts)
        for pat, provider in MX_PROVIDERS:
            if pat in blob:
                return provider
        return hosts[0] if hosts else ""
    except Exception:
        return ""


def ssl_info(host: str) -> tuple[str, str]:
    """(issuer_org, expiry) from a TLS handshake. Never raises."""
    if not host:
        return "", ""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=8) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls_sock:
                cert = tls_sock.getpeercert()
        issuer = dict(x[0] for x in cert.get("issuer", ())).get("organizationName", "")
        return issuer, cert.get("notAfter", "")
    except Exception:
        return "", ""


async def enrich_domain(sem: asyncio.Semaphore, host: str) -> tuple[str, str, str]:
    async with sem:
        email, (issuer, expiry) = await asyncio.gather(
            asyncio.to_thread(mx_provider, host),
            asyncio.to_thread(ssl_info, host),
        )
        return email, issuer, expiry


async def fetch_one(sem: asyncio.Semaphore, client: httpx.AsyncClient,
                     insecure_client: httpx.AsyncClient, domain: str) -> tuple[str, dict, str]:
    async with sem:
        last_err = ""
        for url in url_variants(domain):
            try:
                resp = await client.get(url, headers=FETCH_HEADERS, follow_redirects=True,
                                         timeout=FETCH_TIMEOUT)
                return resp.text, {str(k): str(v) for k, v in resp.headers.items()}, ""
            except Exception as e:
                last_err = repr(e)
                if "CERTIFICATE_VERIFY_FAILED" in last_err:
                    try:
                        resp = await insecure_client.get(
                            url, headers=FETCH_HEADERS, follow_redirects=True,
                            timeout=FETCH_TIMEOUT)
                        return resp.text, {str(k): str(v) for k, v in resp.headers.items()}, ""
                    except Exception as e2:
                        last_err = repr(e2)
        return "", {}, last_err


async def fetch_batch(domains: list[str]) -> list[tuple[str, dict, str]]:
    sem = asyncio.Semaphore(FETCH_CONCURRENCY)
    limits = httpx.Limits(max_connections=FETCH_CONCURRENCY + 20)
    async with httpx.AsyncClient(limits=limits) as client, \
               httpx.AsyncClient(limits=limits, verify=False) as insecure_client:
        return await asyncio.gather(*[fetch_one(sem, client, insecure_client, d) for d in domains])


def pick_column(header: list[str], candidates: set[str]) -> int | None:
    lowered = [h.strip().lower().lstrip("﻿") for h in header]
    for i, h in enumerate(lowered):
        if h in candidates:
            return i
    for i, h in enumerate(lowered):
        if any(c in h for c in candidates):
            return i
    return None


def load_rows(path: str) -> list[dict[str, str]]:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        header = [str(c) if c is not None else "" for c in next(rows_iter)]
        raw_rows = [[str(c) if c is not None else "" for c in row] for row in rows_iter]
        wb.close()
    else:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = list(csv.reader(f))
        if not reader:
            return []
        header, raw_rows = reader[0], reader[1:]

    name_idx = pick_column(header, NAME_HEADERS)
    site_idx = pick_column(header, WEBSITE_HEADERS)
    country_idx = pick_column(header, COUNTRY_HEADERS)
    if site_idx is None:
        raise ValueError(
            f"Couldn't find a website/domain column in header: {header}. "
            f"Expected one of: {sorted(WEBSITE_HEADERS)}"
        )

    rows = []
    for raw in raw_rows:
        website = raw[site_idx].strip() if site_idx < len(raw) else ""
        if not website:
            continue
        rows.append({
            "company": raw[name_idx].strip() if name_idx is not None and name_idx < len(raw) else "",
            "website": website,
            "country": raw[country_idx].strip() if country_idx is not None and country_idx < len(raw) else "",
        })
    return rows


def build_row(src: dict[str, str], status: str, html_size: int, error: str, tech: dict) -> dict[str, str]:
    detections = tech.get("detections", [])
    wapp_cols = tech.get("_wapp_cols", {})

    curated_cols: dict[str, list[tuple[str, str]]] = {}
    for d in detections:
        col = curated_column(d)
        if col:
            curated_cols.setdefault(col, []).append((d["name"], d["confidence"]))

    row = {
        "company": src["company"],
        "website": src["website"],
        "country": src["country"],
        "status": status,
        "html_size": str(html_size),
        "error": (error or "")[:200],
        "email_hosting": tech.get("_email", ""),
        "ssl_issuer": tech.get("_ssl_issuer", ""),
        "ssl_expiry": tech.get("_ssl_expiry", ""),
        "detection_count": str(len(detections)),
        "recommended_count": str(sum(1 for d in detections if d.get("recommended"))),
        "detections": "; ".join(f"{d['name']} ({d['confidence']})" for d in detections),
        "high_confidence": ", ".join(d["name"] for d in detections if d["confidence"] == "high"),
        "medium_confidence": ", ".join(d["name"] for d in detections if d["confidence"] == "medium"),
        "low_confidence": ", ".join(d["name"] for d in detections if d["confidence"] == "low"),
        "wappalyzer_techs": "|".join(tech.get("_wapp_flat", [])),
    }
    for col in TECH_COLUMNS:
        row[col] = merge_column(curated_cols.get(col, []), wapp_cols.get(col, []))
    return row


def resolve_input_path() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    path = input("Enter the path to the CSV or Excel file with companies/websites: ").strip().strip('"')
    if not path:
        print("No file provided, exiting.")
        sys.exit(1)
    return path


def main() -> None:
    input_path = resolve_input_path()
    if not os.path.isfile(input_path):
        print(f"File not found: {input_path}")
        sys.exit(1)

    stem, _ = os.path.splitext(input_path)
    output_csv = f"{stem}_tech_stack.csv"

    print(f"Loading rows from {input_path} ...")
    rows = load_rows(input_path)
    print(f"Loaded {len(rows)} rows with a website value.\n")
    if not rows:
        print("Nothing to process.")
        return

    t0 = time.time()
    print(f"Fetching {len(rows)} sites (concurrency={FETCH_CONCURRENCY}, timeout={FETCH_TIMEOUT}s) ...")
    fetch_results = asyncio.run(fetch_batch([r["website"] for r in rows]))
    print(f"Fetch done in {time.time() - t0:.0f}s\n")

    output_rows: list[dict[str, str]] = []
    pending: list[tuple[dict[str, str], dict, str, dict]] = []  # src, tech, html, headers

    for src, (html, headers, error) in zip(rows, fetch_results):
        if error:
            output_rows.append(build_row(src, "FETCH_ERROR", 0, error, {}))
        elif not html or len(html) < 200:
            output_rows.append(build_row(src, "EMPTY", len(html or ""), "", {}))
        else:
            tech = TechParser(html, headers).detect()
            pending.append((src, tech, html, headers))

    if pending:
        print(f"Running Wappalyzer fingerprinting on {len(pending)} pages "
              f"(workers={WAPP_WORKERS}) ...")
        tw = time.time()
        with ProcessPoolExecutor(max_workers=WAPP_WORKERS, initializer=wapp_init) as pool:
            wapp_results = list(pool.map(
                wapp_worker, [(p[0]["website"], p[2], p[3]) for p in pending]))
        print(f"Wappalyzer done in {time.time() - tw:.0f}s\n")

        print(f"Looking up MX/SSL info for {len(pending)} domains ...")
        te = time.time()

        async def run_enrich():
            sem = asyncio.Semaphore(ENRICH_CONCURRENCY)
            return await asyncio.gather(
                *[enrich_domain(sem, clean_host(p[0]["website"])) for p in pending])

        enrich_results = asyncio.run(run_enrich())
        print(f"Enrichment done in {time.time() - te:.0f}s\n")

        for (src, tech, html, _), full, (email, issuer, expiry) in zip(pending, wapp_results, enrich_results):
            tech["_wapp_cols"] = wapp_by_column(full)
            tech["_wapp_flat"] = sorted(full)
            tech["_email"] = email
            tech["_ssl_issuer"] = issuer
            tech["_ssl_expiry"] = expiry
            output_rows.append(build_row(src, "OK", len(html), "", tech))

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)

    ok = sum(1 for r in output_rows if r["status"] == "OK")
    print("=" * 60)
    print(f"Total rows:    {len(output_rows)}")
    print(f"Successful:    {ok}")
    print(f"Fetch errors:  {sum(1 for r in output_rows if r['status'] == 'FETCH_ERROR')}")
    print(f"Empty:         {sum(1 for r in output_rows if r['status'] == 'EMPTY')}")
    print(f"Total time:    {time.time() - t0:.0f}s")
    print(f"Results saved: {output_csv}")


if __name__ == "__main__":
    main()
