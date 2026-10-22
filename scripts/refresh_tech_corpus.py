"""Fetch/refresh labeled fixtures for the tech-detection validation corpus.

Fixtures live in tests/fixtures/tech_corpus/<name>.json.gz:

    {
      "url": "...", "fetched_at": "...", "rendered": false,
      "html": "...", "headers": {...}, "dns": {"ns": "...", "mx": "...", "a": "..."},
      "cert_issuer": "...",
      "labels": {
        "present": [{"name": "WordPress", "category": "cms", "source": "why we know"}],
        "absent":  [{"name": "WooCommerce", "reason": "logo mention only"}]
      }
    }

Labels are HAND-CURATED ground truth and are never auto-overwritten: a
refresh replaces html/headers/dns/cert only, and reports drift (a present
label the engine no longer detects) for human review.

Usage:
    python scripts/refresh_tech_corpus.py                    # refresh existing
    python scripts/refresh_tech_corpus.py --add stripe.com   # scaffold a fixture
"""

import argparse
import asyncio
import gzip
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

CORPUS_DIR = REPO_ROOT / "tests" / "fixtures" / "tech_corpus"


def _slug(domain: str) -> str:
    return domain.replace("https://", "").replace("http://", "").strip("/").replace("/", "_")


async def _gather_intel(domain: str) -> tuple[dict, str]:
    from src.services.dns_intel import lookup_domain_intel
    from src.services.ssl_intel import inspect_certificate

    dns_intel, ssl_intel = await asyncio.gather(
        lookup_domain_intel(domain), inspect_certificate(domain)
    )
    dns = {
        "ns": " ".join(dns_intel.nameservers),
        "mx": " ".join(dns_intel.mx_hosts),
        "a": " ".join(dns_intel.a_records),
    }
    return dns, ssl_intel.issuer or ""


def _fetch(url: str) -> tuple[str, dict]:
    import httpx

    with httpx.Client(
        timeout=25.0, follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; LakeStreamCorpus/1.0)"},
    ) as client:
        resp = client.get(url)
    return resp.text, dict(resp.headers)


def refresh_fixture(path: Path) -> None:
    from src.scraping.parser.tech_engine import detect, extract_page_signals

    with gzip.open(path, "rt", encoding="utf-8") as f:
        fixture = json.load(f)

    domain = _slug(fixture["url"])
    html, headers = _fetch(fixture["url"])
    dns, cert_issuer = asyncio.run(_gather_intel(domain.split("/")[0]))

    fixture.update(
        html=html, headers=headers, dns=dns, cert_issuer=cert_issuer,
        fetched_at=datetime.now(UTC).isoformat(), rendered=False,
    )
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(fixture, f)

    # Drift report — labels are never touched automatically.
    detected = {
        d.name.lower()
        for d in detect(extract_page_signals(
            html, url=fixture["url"], headers=headers, dns=dns, cert_issuer=cert_issuer
        ))
    }
    for label in (fixture.get("labels") or {}).get("present", []):
        if label["name"].lower() not in detected:
            print(f"  DRIFT {path.name}: labeled-present '{label['name']}' not detected "
                  "— review the label or the catalog.")
    print(f"  refreshed {path.name} ({len(html) // 1024} KB)")


def add_fixture(domain: str) -> None:
    url = domain if domain.startswith("http") else f"https://{domain}"
    name = _slug(url)
    path = CORPUS_DIR / f"{name}.json.gz"
    if path.exists():
        print(f"{path.name} already exists — refreshing instead.")
        refresh_fixture(path)
        return

    html, headers = _fetch(url)
    dns, cert_issuer = asyncio.run(_gather_intel(name.split("/")[0]))
    fixture = {
        "url": url,
        "fetched_at": datetime.now(UTC).isoformat(),
        "rendered": False,
        "html": html,
        "headers": headers,
        "dns": dns,
        "cert_issuer": cert_issuer,
        "labels": {"present": [], "absent": []},
    }
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(fixture, f)
    print(f"  created {path.name} — now hand-curate its labels before scoring.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--add", action="append", default=[], metavar="DOMAIN")
    args = parser.parse_args()

    if args.add:
        for domain in args.add:
            add_fixture(domain)
        return

    fixtures = sorted(CORPUS_DIR.glob("*.json.gz"))
    if not fixtures:
        print(f"No fixtures in {CORPUS_DIR}. Add some with --add <domain>.")
        sys.exit(1)
    for path in fixtures:
        try:
            refresh_fixture(path)
        except Exception as e:
            print(f"  FAILED {path.name}: {e}")


if __name__ == "__main__":
    main()
