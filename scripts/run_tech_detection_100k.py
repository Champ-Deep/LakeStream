#!/usr/bin/env python3
"""100k-scale tech detection v2: fetch + TechParser + Wappalyzer + MX/SSL.

- Stream-reads 100k_websites.xlsx (read_only mode)
- 250 concurrent fetches, 20s timeout
- Checkpoint-append to CSV every CHECKPOINT_EVERY rows; resume skips done domains
- Wappalyzer (3999 fingerprints) in a process pool; categories merged into columns
- Email hosting via MX lookup, SSL issuer/expiry via TLS handshake
- No LLM pass (dropped: Wappalyzer covers the empty-detection gap)
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
from openpyxl import load_workbook

from src.scraping.parser.tech_parser import TechParser
from src.scraping.parser.wappalyzer_runner import wapp_by_column, wapp_init, wapp_worker

INPUT_XLSX = os.path.join(os.path.dirname(__file__), "..", "100k_websites.xlsx")
OUTPUT_CSV = os.path.join(os.path.dirname(__file__), "..", "100k_tech_stacks_v2_final.csv")

FETCH_CONCURRENCY = 250
FETCH_TIMEOUT = 20
CHECKPOINT_EVERY = 100
WAPP_WORKERS = int(os.environ.get("WAPP_WORKERS", "7"))  # CPU-bound regex scan
ENRICH_CONCURRENCY = 100  # MX/SSL lookups
SMOKE_LIMIT = int(os.environ.get("SMOKE_LIMIT", "0"))  # 0 = full run

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

# Merged tech columns: curated detections carry (confidence), Wappalyzer-only
# names are appended plain. Order = CSV column order.
TECH_COLUMNS = [
    "platform", "frameworks", "js_libraries", "analytics", "cdn",
    "hosting", "web_servers", "os", "programming_languages", "widgets",
]

# curated TechParser category -> column (those not listed have no curated source)
CURATED_CATEGORY_MAP = {
    "cms": "platform",
    "ecommerce": "platform",
    "framework": "frameworks",
    "js_library": "js_libraries",
    "analytics": "analytics",
    "cdn": "cdn",
    "hosting": "hosting",
}

# backend category is a mix; route by name
_BACKEND_WEB_SERVERS = {"nginx", "apache", "envoy"}
_BACKEND_LANGUAGES = {"php", "asp.net", "java/spring"}


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

FIELDS = [
    "PARTY_NAME", "WEB_ADDRESS", "COUNTRY",
    "status", "html_size", "error",
    *TECH_COLUMNS,
    "email_hosting", "ssl_issuer", "ssl_expiry",
    "detection_count", "recommended_count", "detections",
    "high_confidence", "medium_confidence", "low_confidence",
    "wappalyzer_techs",
]

MX_PROVIDERS = [
    ("google.com", "Google Workspace"), ("googlemail.com", "Google Workspace"),
    ("outlook.com", "Microsoft 365"), ("microsoft.com", "Microsoft 365"),
    ("zoho", "Zoho Mail"), ("mimecast", "Mimecast"),
    ("proofpoint", "Proofpoint"), ("pphosted", "Proofpoint"),
    ("messagelabs", "Broadcom Email Security"), ("barracuda", "Barracuda"),
    ("amazonses", "Amazon SES"), ("mailgun", "Mailgun"), ("sendgrid", "SendGrid"),
    ("secureserver", "GoDaddy"), ("ionos", "IONOS"), ("one.com", "One.com"),
    ("ovh", "OVH"), ("infomaniak", "Infomaniak"), ("proton", "Proton Mail"),
    ("fastmail", "Fastmail"), ("yandex", "Yandex"), ("qq.com", "Tencent QQ"),
    ("163.com", "NetEase 163"), ("alibaba", "Alibaba Mail"),
    ("kundenserver", "IONOS"), ("register.it", "Register.it"),
    ("aruba.it", "Aruba"), ("hetzner", "Hetzner"), ("strato", "STRATO"),
]


def sanitize(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, (list, tuple)):
        return ", ".join(str(x) for x in val)
    return str(val)


def clean_host(domain: str) -> str:
    host = domain.strip()
    for scheme in ("https://", "http://"):
        if host.startswith(scheme):
            host = host[len(scheme):]
    host = host.split("/")[0].split(":")[0].strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def mx_provider(host: str) -> str:
    """Email hosting provider from MX records. Never raises."""
    if not host:
        return ""
    try:
        answers = dns.resolver.resolve(host, "MX", lifetime=5)
        hosts = sorted({str(r.exchange).lower().rstrip(".") for r in answers})
        blob = " ".join(hosts)
        for pat, name in MX_PROVIDERS:
            if pat in blob:
                return name
        return hosts[0] if hosts else ""
    except Exception:
        return ""


def ssl_info(host: str) -> tuple[str, str]:
    """(issuer_org, expiry) from TLS handshake. Never raises."""
    if not host:
        return "", ""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=8) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                cert = ss.getpeercert()
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


def merge_column(curated: list[tuple[str, str]], wapp_names: list[str]) -> str:
    """curated [(name, confidence)] first, then Wappalyzer-only names plain."""
    seen = {n.lower() for n, _ in curated}
    parts = [f"{n} ({c})" for n, c in curated]
    parts.extend(n for n in wapp_names if n.lower() not in seen)
    return "|".join(parts)


def build_row(src: dict[str, Any], status: str, html_size: int, error: str,
              tech: dict) -> dict[str, str]:
    detections = tech.get("detections", [])
    wapp_cols = tech.get("_wapp_cols", {})

    # curated per-column (name, confidence) pairs, first-seen order
    curated_cols: dict[str, list[tuple[str, str]]] = {}
    for d in detections:
        col = curated_column(d)
        if col:
            curated_cols.setdefault(col, []).append((d["name"], d["confidence"]))

    row = {
        "PARTY_NAME": sanitize(src.get("PARTY_NAME")),
        "WEB_ADDRESS": sanitize(src.get("WEB_ADDRESS")),
        "COUNTRY": sanitize(src.get("COUNTRY")),
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


def read_done_domains() -> set[str]:
    if not os.path.exists(OUTPUT_CSV):
        return set()
    done = set()
    with open(OUTPUT_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("status"):
                done.add((r.get("WEB_ADDRESS") or "").strip().lower())
    return done


class CsvWriter:
    def __init__(self, path: str):
        exists = os.path.exists(path)
        self.f = open(path, "a", newline="", encoding="utf-8")
        self.w = csv.DictWriter(self.f, fieldnames=FIELDS)
        if not exists:
            self.w.writeheader()
            self.f.flush()

    def append(self, rows: list[dict[str, str]]) -> None:
        for r in rows:
            self.w.writerow(r)
        self.f.flush()

    def close(self) -> None:
        self.f.close()


async def fetch_one(sem: asyncio.Semaphore, client: httpx.AsyncClient,
                    domain: str) -> tuple[str, dict[str, str], str]:
    async with sem:
        url = domain if domain.startswith(("http://", "https://")) else f"https://{domain}"
        try:
            resp = await client.get(url, headers=FETCH_HEADERS, follow_redirects=True,
                                    timeout=FETCH_TIMEOUT)
            return resp.text, {str(k): str(v) for k, v in resp.headers.items()}, ""
        except Exception as e:
            return "", {}, repr(e)


async def fetch_batch(rows: list[dict[str, Any]]) -> list[tuple[str, dict[str, str], str]]:
    sem = asyncio.Semaphore(FETCH_CONCURRENCY)
    limits = httpx.Limits(max_connections=FETCH_CONCURRENCY + 50,
                          max_keepalive_connections=FETCH_CONCURRENCY)
    async with httpx.AsyncClient(limits=limits) as client:
        return await asyncio.gather(
            *[fetch_one(sem, client, str(r.get("WEB_ADDRESS", "")).strip()) for r in rows]
        )


def load_rows() -> list[dict[str, Any]]:
    wb = load_workbook(INPUT_XLSX, read_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    header = [str(c).lstrip("\ufeff") if c else "" for c in next(it)]
    rows = [dict(zip(header, cells)) for cells in it]
    wb.close()
    return rows


def main() -> None:
    t0 = time.time()
    rows = load_rows()
    if SMOKE_LIMIT:
        rows = rows[:SMOKE_LIMIT]
        print(f"SMOKE MODE: first {SMOKE_LIMIT} rows")
    print(f"Loaded {len(rows)} rows")

    done = read_done_domains()
    todo = [r for r in rows if str(r.get("WEB_ADDRESS", "")).strip().lower() not in done]
    print(f"Already done: {len(done)} | To fetch: {len(todo)}")

    writer = CsvWriter(OUTPUT_CSV)
    buffer: list[dict[str, str]] = []
    processed = 0

    try:
        with ProcessPoolExecutor(max_workers=WAPP_WORKERS, initializer=wapp_init) as pool:
            for start in range(0, len(todo), CHECKPOINT_EVERY):
                chunk = todo[start:start + CHECKPOINT_EVERY]
                results = asyncio.run(fetch_batch(chunk))
                pending = []  # (src, tech, html, domain, headers)
                for src, (html, headers, error) in zip(chunk, results):
                    if error:
                        buffer.append(build_row(src, "FETCH_ERROR", 0, error, {}))
                    elif not html or len(html) < 200:
                        buffer.append(build_row(src, "EMPTY", len(html or ""), "", {}))
                    else:
                        tech = TechParser(html, headers).detect()
                        pending.append((src, tech, html,
                                        str(src.get("WEB_ADDRESS", "")).strip(), headers))

                # Wappalyzer (CPU) in process pool
                tasks = [(p[3], p[2], p[4]) for p in pending]
                wapp_results = list(pool.map(wapp_worker, tasks))

                # MX + SSL (I/O) async
                async def run_enrich():
                    sem = asyncio.Semaphore(ENRICH_CONCURRENCY)
                    return await asyncio.gather(
                        *[enrich_domain(sem, clean_host(p[3])) for p in pending])
                enrich = asyncio.run(run_enrich()) if pending else []

                for (src, tech, html, _, _), full, (email, issuer, expiry) in \
                        zip(pending, wapp_results, enrich):
                    tech["_wapp_cols"] = wapp_by_column(full)
                    tech["_wapp_flat"] = sorted(full)
                    tech["_email"] = email
                    tech["_ssl_issuer"] = issuer
                    tech["_ssl_expiry"] = expiry
                    buffer.append(build_row(src, "OK", len(html), "", tech))

                writer.append(buffer)
                buffer.clear()
                processed += len(chunk)
                rate = processed / (time.time() - t0)
                print(f"[checkpoint] {processed}/{len(todo)} done "
                      f"({rate:.0f}/s, elapsed {time.time()-t0:.0f}s)")
    except KeyboardInterrupt:
        writer.append(buffer)
        writer.close()
        print(f"\nInterrupted. {processed} rows saved. Rerun to resume.")
        sys.exit(1)
    writer.close()

    # Summary
    total = ok = ferr = empty = 0
    with open(OUTPUT_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            total += 1
            ok += r["status"] == "OK"
            ferr += r["status"] == "FETCH_ERROR"
            empty += r["status"] == "EMPTY"
    print("\n" + "=" * 60)
    print(f"Total: {total} | OK: {ok} | FETCH_ERROR: {ferr} | EMPTY: {empty}")
    print(f"Time: {time.time()-t0:.0f}s | Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
