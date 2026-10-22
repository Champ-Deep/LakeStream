"""Coverage-gap analysis against the enthec/webappanalyzer reference ruleset.

================================ LICENSE NOTE =================================
The reference ruleset is GPL-3.0. It is downloaded to the GITIGNORED
.tech-reference/ directory and loaded IN MEMORY only. It must never be
committed, vendored, or shipped — and it is consulted only for WHAT to cover,
never for HOW: every LakeStream signature is authored from first principles
in src/data/tech_signatures.py (see docs/adding-tech-signatures.md).
===============================================================================

Usage:
    python scripts/tech_gap_analysis.py --refresh          # (re)download reference
    python scripts/tech_gap_analysis.py                    # score corpus fixtures
    python scripts/tech_gap_analysis.py --live domains.txt # fetch + compare live

Output: technologies the reference catalog detected that ours missed, ranked
by prevalence across the sample, grouped by category, with the evidence type
that caught them (js/dom-only techs need signals of our own devising).
"""

import argparse
import collections
import gzip
import json
import string
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

REFERENCE_DIR = REPO_ROOT / ".tech-reference" / "webappanalyzer"
CORPUS_DIR = REPO_ROOT / "tests" / "fixtures" / "tech_corpus"
BASE_URL = "https://raw.githubusercontent.com/enthec/webappanalyzer/main/src"

LICENSE_BANNER = (
    "\n*** GPL-3.0 reference data: in-memory use only — never commit, vendor, "
    "or copy patterns from it. ***\n"
)


def refresh_reference() -> None:
    import httpx

    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    names = ["categories.json"] + [
        f"technologies/{c}.json" for c in ("_", *string.ascii_lowercase)
    ]
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for name in names:
            resp = client.get(f"{BASE_URL}/{name}")
            if resp.status_code != 200:
                print(f"  skip {name}: HTTP {resp.status_code}")
                continue
            out = REFERENCE_DIR / Path(name).name
            out.write_text(resp.text, encoding="utf-8")
            print(f"  fetched {name} -> {out.relative_to(REPO_ROOT)}")
    (REFERENCE_DIR / "DO-NOT-COMMIT.txt").write_text(
        "GPL-3.0 reference data (enthec/webappanalyzer). This directory is "
        "gitignored on purpose. See scripts/tech_gap_analysis.py.\n"
    )


def load_reference_catalog():
    from src.scraping.parser.tech_engine import _Catalog, _load_wappalyzer_catalog

    if not REFERENCE_DIR.exists():
        print("Reference data missing — run with --refresh first.")
        sys.exit(1)
    meta: dict = {}
    sigs = _load_wappalyzer_catalog(REFERENCE_DIR, meta_out=meta)
    return _Catalog(sigs, meta=meta)


def iter_corpus_fixtures():
    for path in sorted(CORPUS_DIR.glob("*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            yield path.stem.removesuffix(".json"), json.load(f)


def fetch_live(domain: str) -> dict | None:
    import httpx

    url = f"https://{domain.strip().strip('/')}"
    try:
        with httpx.Client(
            timeout=20.0, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; LakeStreamGap/1.0)"},
        ) as client:
            resp = client.get(url)
        return {"url": url, "html": resp.text, "headers": dict(resp.headers)}
    except Exception as e:
        print(f"  fetch failed for {domain}: {e}")
        return None


def analyze(samples: list[tuple[str, dict]]) -> None:
    from src.scraping.parser.tech_engine import detect, extract_page_signals

    ref_catalog = load_reference_catalog()
    missed_counter: collections.Counter = collections.Counter()
    missed_info: dict[str, tuple[str, set[str]]] = {}
    total_ours = total_ref = 0

    for name, sample in samples:
        signals = extract_page_signals(
            sample.get("html", ""),
            url=sample.get("url", f"https://{name}"),
            headers=sample.get("headers") or {},
            dns=sample.get("dns") or {},
            cert_issuer=sample.get("cert_issuer", ""),
        )
        ours = {d.name.strip().lower() for d in detect(signals)}
        ref_dets = detect(signals, catalog=ref_catalog)
        total_ours += len(ours)
        total_ref += len(ref_dets)

        for det in ref_dets:
            key = det.name.strip().lower()
            if key in ours:
                continue
            missed_counter[key] += 1
            cat, sources = missed_info.setdefault(key, (det.category, set()))
            sources.add(det.source)

    print(f"\nSamples: {len(samples)} | our detections: {total_ours} | "
          f"reference detections: {total_ref}")
    if not missed_counter:
        print("No coverage gaps found on this sample.")
        return

    by_category: dict[str, list[tuple[str, int, set[str]]]] = collections.defaultdict(list)
    for key, count in missed_counter.items():
        cat, sources = missed_info[key]
        by_category[cat].append((key, count, sources))

    print(f"\n=== Missed technologies ({len(missed_counter)}), by category ===")
    for cat in sorted(by_category, key=lambda c: -sum(x[1] for x in by_category[c])):
        rows = sorted(by_category[cat], key=lambda x: -x[1])
        print(f"\n[{cat}] ({sum(r[1] for r in rows)} hits)")
        for key, count, sources in rows:
            print(f"  {count:3d}x  {key:40s} via {','.join(sorted(sources))}")
    print(
        "\nAuthor coverage for these in src/data/tech_signatures.py from vendor "
        "docs / live inspection — never by copying reference patterns."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="(Re)download the reference ruleset")
    parser.add_argument("--live", metavar="FILE", help="File of domains to fetch live and compare")
    args = parser.parse_args()

    print(LICENSE_BANNER)
    if args.refresh:
        refresh_reference()
        if not args.live and not CORPUS_DIR.exists():
            return

    samples: list[tuple[str, dict]] = []
    if args.live:
        for domain in Path(args.live).read_text().split():
            fetched = fetch_live(domain)
            if fetched:
                samples.append((domain, fetched))
    else:
        samples = list(iter_corpus_fixtures())
        if not samples:
            print(f"No corpus fixtures in {CORPUS_DIR} — build some with "
                  "scripts/refresh_tech_corpus.py or pass --live domains.txt")
            sys.exit(1)

    analyze(samples)


if __name__ == "__main__":
    main()
