"""Score the tech-detection engine against the labeled validation corpus.

Metrics (labels are partial ground truth, so the vocabulary matters):

- recall           — fraction of labeled-PRESENT technologies detected at an
                     asserted tier (high/medium; "low" is audit-only).
- violations       — detections at an asserted tier of a labeled-ABSENT
                     technology. Every violation is a confirmed false
                     positive; the gate requires zero.
- structural depth — detections/site from evidence classes a browser-bound
                     detector cannot produce (DNS records, TLS cert issuer):
                     one of the "surpass Wappalyzer" criteria.

Usage:
    python scripts/score_tech_corpus.py
    python scripts/score_tech_corpus.py --catalog .tech-reference/webappanalyzer
    python scripts/score_tech_corpus.py --write-baseline
    python scripts/score_tech_corpus.py --check     # gate: exit 1 on regression

--check compares against scripts/corpus_baseline.json (our scores only — no
third-party data): violations must be 0 and recall must not drop more than
2 points below the baseline.
"""

import argparse
import collections
import gzip
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

CORPUS_DIR = REPO_ROOT / "tests" / "fixtures" / "tech_corpus"
BASELINE_PATH = REPO_ROOT / "scripts" / "corpus_baseline.json"

ASSERTED_TIERS = ("high", "medium")


def load_fixtures() -> list[tuple[str, dict]]:
    fixtures = []
    for path in sorted(CORPUS_DIR.glob("*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            fixtures.append((path.name.removesuffix(".json.gz"), json.load(f)))
    return fixtures


def score(catalog=None) -> dict:
    from src.scraping.parser.tech_engine import detect, extract_page_signals

    fixtures = load_fixtures()
    if not fixtures:
        print(f"No fixtures in {CORPUS_DIR} — build the corpus first "
              "(scripts/refresh_tech_corpus.py --add <domain>).")
        sys.exit(1)

    hits = misses = 0
    violations: list[str] = []
    missed: list[str] = []
    per_category_hits: collections.Counter = collections.Counter()
    per_category_total: collections.Counter = collections.Counter()
    evidence_counts: collections.Counter = collections.Counter()
    structural_sites = 0

    for name, fx in fixtures:
        signals = extract_page_signals(
            fx.get("html", ""), url=fx.get("url", ""),
            headers=fx.get("headers") or {}, dns=fx.get("dns") or {},
            cert_issuer=fx.get("cert_issuer", ""),
        )
        detections = detect(signals, catalog=catalog)
        asserted = {
            d.name.strip().lower() for d in detections if d.confidence in ASSERTED_TIERS
        }
        for d in detections:
            evidence_counts[d.source] += 1
        if any(d.source in ("dns", "cert_issuer") for d in detections):
            structural_sites += 1

        labels = fx.get("labels") or {}
        for label in labels.get("present", []):
            cat = label.get("category", "other")
            per_category_total[cat] += 1
            if label["name"].strip().lower() in asserted:
                hits += 1
                per_category_hits[cat] += 1
            else:
                misses += 1
                missed.append(f"{name}: {label['name']}")
        for label in labels.get("absent", []):
            if label["name"].strip().lower() in asserted:
                violations.append(f"{name}: {label['name']} ({label.get('reason', '')})")

    total_labels = hits + misses
    recall = hits / total_labels if total_labels else 0.0
    return {
        "fixtures": len(fixtures),
        "labeled_present": total_labels,
        "recall": round(recall, 4),
        "violations": violations,
        "missed": missed,
        "per_category": {
            cat: f"{per_category_hits[cat]}/{per_category_total[cat]}"
            for cat in sorted(per_category_total)
        },
        "evidence_counts": dict(evidence_counts.most_common()),
        "sites_with_dns_tls_evidence": structural_sites,
    }


def print_report(result: dict, title: str) -> None:
    print(f"\n=== {title} ===")
    print(f"fixtures                : {result['fixtures']}")
    print(f"labeled present         : {result['labeled_present']}")
    print(f"recall                  : {result['recall']:.1%}")
    print(f"absent-label violations : {len(result['violations'])}")
    for v in result["violations"]:
        print(f"  FP: {v}")
    if result["missed"]:
        print("missed present-labels   :")
        for m in result["missed"]:
            print(f"  {m}")
    print(f"per-category recall     : {result['per_category']}")
    print(f"evidence mix            : {result['evidence_counts']}")
    print(f"sites w/ DNS+TLS proof  : {result['sites_with_dns_tls_evidence']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", metavar="PATH",
                        help="Also score an external Wappalyzer-format catalog (in memory)")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--check", action="store_true",
                        help="Exit 1 if violations > 0 or recall drops >2pts vs baseline")
    args = parser.parse_args()

    ours = score()
    print_report(ours, "LakeStream native catalog")

    if args.catalog:
        from src.scraping.parser.tech_engine import _Catalog, _load_wappalyzer_catalog

        meta: dict = {}
        sigs = _load_wappalyzer_catalog(Path(args.catalog), meta_out=meta)
        ref = score(catalog=_Catalog(sigs, meta=meta))
        print_report(ref, f"reference catalog ({args.catalog})")
        print("\n--- surpass criteria (ours vs reference) ---")
        print(f"recall     : {ours['recall']:.1%} vs {ref['recall']:.1%} "
              f"({'OK' if ours['recall'] >= ref['recall'] else 'BEHIND'})")
        print(f"violations : {len(ours['violations'])} vs {len(ref['violations'])} "
              f"({'OK' if len(ours['violations']) <= len(ref['violations']) else 'BEHIND'})")
        print(f"DNS/TLS    : {ours['sites_with_dns_tls_evidence']} vs "
              f"{ref['sites_with_dns_tls_evidence']} sites")

    if args.write_baseline:
        baseline = {"recall": ours["recall"], "violations": len(ours["violations"]),
                    "labeled_present": ours["labeled_present"], "fixtures": ours["fixtures"]}
        BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n")
        print(f"\nBaseline written to {BASELINE_PATH}")

    if args.check:
        if not BASELINE_PATH.exists():
            print("\nNo baseline — run --write-baseline first.")
            sys.exit(1)
        baseline = json.loads(BASELINE_PATH.read_text())
        failed = False
        if ours["violations"]:
            print(f"\nCHECK FAIL: {len(ours['violations'])} absent-label violations (must be 0).")
            failed = True
        if ours["recall"] < baseline["recall"] - 0.02:
            print(f"\nCHECK FAIL: recall {ours['recall']:.1%} dropped more than 2pts below "
                  f"baseline {baseline['recall']:.1%}.")
            failed = True
        if failed:
            sys.exit(1)
        print("\nCHECK OK: zero violations, recall within budget.")


if __name__ == "__main__":
    main()
