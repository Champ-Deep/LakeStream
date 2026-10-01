#!/usr/bin/env python3
"""128k-scale tech detection: fetch + TechParser + Wappalyzer + MX/SSL.

- Stream-reads 128k_companies.xlsx (read_only mode)
- 250 concurrent fetches, 20s timeout
- Checkpoint-append to CSV every 100 rows; resume skips done domains
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
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from typing import Any

import dns.resolver
import httpx
from openpyxl import load_workbook

from src.scraping.parser.tech_parser import TechParser
from src.scraping.parser.wappalyzer_runner import detect_wappalyzer_full, wapp_by_column, wapp_init

INPUT_XLSX = os.path.join(os.path.dirname(__file__), "..", "128k_companies.xlsx")
OUTPUT_CSV = os.path.join(os.path.dirname(__file__), "..", "128k_companies_v2.csv")
SEED_CSV = os.path.join(os.path.dirname(__file__), "..", "128k_companies_seed.csv")

FETCH_CONCURRENCY = 250
FETCH_TIMEOUT = 20
CHECKPOINT_EVERY = 500
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
    return host.lower()


def url_variants(domain: str) -> list[str]:
    host = clean_host(domain)
    return [f"https://{host}", f"https://www.{host}"]


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


def load_seed() -> dict[str, dict[str, str]]:
    """Load seed CSV (best row per host) as in-memory host→row map for clones."""
    if not os.path.exists(SEED_CSV):
        return {}
    host_map: dict[str, dict[str, str]] = {}
    with open(SEED_CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            h = clean_host(r.get("WEB_ADDRESS", ""))
            if h and h not in host_map:
                host_map[h] = r
    return host_map


def read_done(path: str) -> tuple[Counter, dict[str, dict[str, str]]]:
    """Read existing output CSV → done-tuple counter + host→row map for resume."""
    done: Counter = Counter()
    host_map: dict[str, dict[str, str]] = {}
    if not os.path.exists(path):
        return done, host_map
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            h = clean_host(r.get("WEB_ADDRESS", ""))
            if h:
                w = (r.get("WEB_ADDRESS") or "").strip()
                n = (r.get("PARTY_NAME") or "").strip()
                c = (r.get("COUNTRY") or "").strip()
                done[(h, n, c)] += 1
                if h not in host_map or host_map[h].get("status") != "OK":
                    host_map[h] = r
    return done, host_map


def build_clone(src: dict[str, Any], seed_row: dict[str, str]) -> dict[str, str]:
    """Clone tech data from seed row with src's identity columns."""
    row = dict(seed_row)
    row["PARTY_NAME"] = sanitize(src.get("PARTY_NAME"))
    row["WEB_ADDRESS"] = sanitize(src.get("WEB_ADDRESS"))
    row["COUNTRY"] = sanitize(src.get("COUNTRY"))
    return row


class CsvWriter:
    def __init__(self, path: str):
        exists = os.path.exists(path)
        self.f = open(path, "a", newline="", encoding="utf-8")
        self.w = csv.DictWriter(self.f, fieldnames=FIELDS)
        if not exists or os.path.getsize(path) == 0:
            self.w.writeheader()
            self.f.flush()

    def append(self, rows: list[dict[str, str]]) -> None:
        for r in rows:
            self.w.writerow(r)
        self.f.flush()

    def close(self) -> None:
        self.f.close()


async def fetch_one(sem: asyncio.Semaphore, client: httpx.AsyncClient,
                    insecure_client: httpx.AsyncClient,
                    domain: str) -> tuple[str, dict[str, str], str]:
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
                    # broken-cert sites are still fingerprintable; read-only GET
                    try:
                        resp = await insecure_client.get(
                            url, headers=FETCH_HEADERS, follow_redirects=True,
                            timeout=FETCH_TIMEOUT)
                        return resp.text, {str(k): str(v) for k, v in resp.headers.items()}, ""
                    except Exception as e2:
                        last_err = repr(e2)
        return "", {}, last_err


async def fetch_batch(domains: list[str]) -> list[tuple[str, dict[str, str], str]]:
    sem = asyncio.Semaphore(FETCH_CONCURRENCY)
    limits = httpx.Limits(max_connections=FETCH_CONCURRENCY + 50,
                          max_keepalive_connections=FETCH_CONCURRENCY)
    async with httpx.AsyncClient(limits=limits) as client, \
               httpx.AsyncClient(limits=limits, verify=False) as insecure_client:
        return await asyncio.gather(
            *[fetch_one(sem, client, insecure_client, d) for d in domains]
        )


def combined_worker(task: tuple[str, str, dict]) -> tuple[str, dict, dict, int]:
    """Pool worker: (host, html, headers) -> (host, tech_dict, wapp_full, html_size).

    Runs TechParser + Wappalyzer sequentially on the same HTML blob in one
    process call, eliminating the serial TechParser bottleneck in main and
    avoiding double-pickling of HTML.
    """
    host, html, headers = task
    try:
        tech = TechParser(html, headers).detect()
        wapp_full = detect_wappalyzer_full(host, html, headers)
        return host, tech, wapp_full, len(html)
    except Exception:
        return host, {"detections": []}, {}, len(html)


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
    print(f"Loaded {len(rows)} rows from {INPUT_XLSX}")

    host_map = load_seed()
    print(f"Seed hosts loaded: {len(host_map)}")

    done_tuples, output_host_map = read_done(OUTPUT_CSV)
    host_map.update(output_host_map)
    num_done_rows = done_tuples.total() if hasattr(done_tuples, 'total') else sum(done_tuples.values())
    print(f"Already in output: {num_done_rows} rows | "
          f"Hosts with data: {len(host_map)}")

    writer = CsvWriter(OUTPUT_CSV)
    buffer: list[dict[str, str]] = []
    processed = 0
    clones_used = 0

    try:
        with ProcessPoolExecutor(max_workers=WAPP_WORKERS, initializer=wapp_init) as pool:
            for start in range(0, len(rows), CHECKPOINT_EVERY):
                chunk = rows[start:start + CHECKPOINT_EVERY]

                needs_clone: list[dict[str, Any]] = []
                needs_fetch: dict[str, list[dict[str, Any]]] = {}  # host -> [src rows]

                for src in chunk:
                    h = clean_host(str(src.get("WEB_ADDRESS", "")))
                    if not h:
                        buffer.append(build_row(src, "FETCH_ERROR", 0, "No WEB_ADDRESS", {}))
                        continue
                    w = str(src.get("WEB_ADDRESS", "")).strip()
                    n = str(src.get("PARTY_NAME", "")).strip()
                    co = str(src.get("COUNTRY", "")).strip()
                    tup = (h, n, co)

                    if done_tuples.get(tup, 0) > 0:
                        done_tuples[tup] -= 1
                        continue  # already in output, skip

                    if h in host_map:
                        needs_clone.append(src)
                    else:
                        needs_fetch.setdefault(h, []).append(src)

                # Emit clones (same host, different company)
                for src in needs_clone:
                    h = clean_host(str(src.get("WEB_ADDRESS", "")))
                    buffer.append(build_clone(src, host_map[h]))
                clones_used += len(needs_clone)

                # Fetch new hosts
                uniq_hosts = list(needs_fetch)
                results = asyncio.run(fetch_batch(uniq_hosts)) if uniq_hosts else []
                pending: list[tuple[str, str, dict]] = []  # (host, html, headers)
                for host, (html, headers, error) in zip(uniq_hosts, results):
                    if error:
                        for srcrt in needs_fetch[host]:
                            buffer.append(build_row(srcrt, "FETCH_ERROR", 0, error, {}))
                    elif not html or len(html) < 200:
                        for srcrt in needs_fetch[host]:
                            buffer.append(build_row(srcrt, "EMPTY", len(html or ""), "", {}))
                    else:
                        pending.append((host, html, headers))

                # Combined TechParser + Wappalyzer (CPU) in process pool
                results_by_host: dict[str, tuple[dict, dict, int]] = {}
                if pending:
                    combined_results = list(pool.map(combined_worker, pending))
                    results_by_host = {h: (t, w, s) for h, t, w, s in combined_results}

                # MX + SSL (I/O) async
                async def run_enrich():
                    sem = asyncio.Semaphore(ENRICH_CONCURRENCY)
                    return await asyncio.gather(
                        *[enrich_domain(sem, h) for h, _, _ in pending])
                enrich = asyncio.run(run_enrich()) if pending else []

                for (host, _, _), (email, issuer, expiry) in zip(pending, enrich):
                    tech, wapp_full, html_size = results_by_host[host]
                    tech["_wapp_cols"] = wapp_by_column(wapp_full)
                    tech["_wapp_flat"] = sorted(wapp_full)
                    tech["_email"] = email
                    tech["_ssl_issuer"] = issuer
                    tech["_ssl_expiry"] = expiry
                    for srcrt in needs_fetch[host]:
                        status = "OK" if tech.get("detections") else "OK_NODETECT"
                        row = build_row(srcrt, status, html_size, "", tech)
                        buffer.append(row)
                    # remember for future clones
                    host_map[host] = row

                writer.append(buffer)
                buffer.clear()
                processed += len(chunk)
                rate = processed / (time.time() - t0) if time.time() > t0 else 0
                print(f"[checkpoint] {processed}/{len(rows)} done "
                      f"({rate:.0f}/s, {clones_used} clones, "
                      f"elapsed {time.time()-t0:.0f}s)")
    except KeyboardInterrupt:
        writer.append(buffer)
        writer.close()
        print(f"\nInterrupted. {processed} rows saved. Rerun to resume.")
        sys.exit(1)
    writer.close()

    # Summary
    total = ok = nodetect = ferr = empty = 0
    with open(OUTPUT_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            total += 1
            ok += r["status"] == "OK"
            nodetect += r["status"] == "OK_NODETECT"
            ferr += r["status"] == "FETCH_ERROR"
            empty += r["status"] == "EMPTY"
    print("\n" + "=" * 60)
    print(f"Total: {total} | OK: {ok} | OK_NODETECT: {nodetect} | FETCH_ERROR: {ferr} | EMPTY: {empty}")
    print(f"Clones emitted: {clones_used} | "
          f"Time: {time.time()-t0:.0f}s | Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
