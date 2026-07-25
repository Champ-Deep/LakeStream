#!/usr/bin/env python3
"""Fetch/prepare an external Wappalyzer-format fingerprint catalog.

LakeStream loads the catalog at RUNTIME from a directory you point it at. The
catalog is deliberately NOT committed into this repository — see
docs/TECH_CATALOG.md for the licensing reason. This script helps you assemble
that directory on your own machine or server.

Usage:
    # Validate + summarise a catalog you already have
    python scripts/import_tech_catalog.py --path ./catalog

    # Merge a directory of per-letter json files into one, then validate
    python scripts/import_tech_catalog.py --path ./src/technologies --merge ./catalog

Then run LakeStream with:
    TECH_CATALOG_PATH=/absolute/path/to/catalog
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

SIGNAL_KEYS = (
    "scriptSrc", "scripts", "html", "url", "headers", "cookies", "meta",
    "dns", "certIssuer", "js", "dom", "text", "css", "robots", "xhr", "probe",
)
# Keys LakeStream's engine can actually evaluate today. `js`/`dom` need a live
# browser context; entries relying only on those will simply never fire.
SUPPORTED_KEYS = (
    "scriptSrc", "scripts", "html", "url", "headers", "cookies", "meta",
    "dns", "certIssuer",
)


def load_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.glob("*.json") if p.name != "categories.json")


def analyse(path: Path) -> int:
    files = load_files(path)
    if not files:
        print(f"error: no .json files found in {path}", file=sys.stderr)
        return 1

    total = 0
    by_signal: Counter[str] = Counter()
    unsupported_only = 0
    bad_files = 0

    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ! unreadable: {f.name}: {e}", file=sys.stderr)
            bad_files += 1
            continue
        if isinstance(data, dict) and isinstance(data.get("technologies"), dict):
            data = data["technologies"]
        if not isinstance(data, dict):
            continue
        for _name, spec in data.items():
            if not isinstance(spec, dict):
                continue
            total += 1
            present = [k for k in SIGNAL_KEYS if spec.get(k)]
            for k in present:
                by_signal[k] += 1
            if present and not any(k in SUPPORTED_KEYS for k in present):
                unsupported_only += 1

    cats = path / "categories.json" if path.is_dir() else path.parent / "categories.json"

    print(f"catalog path       : {path}")
    print(f"files              : {len(files)}" + (f"  ({bad_files} unreadable)" if bad_files else ""))
    print(f"technologies       : {total}")
    print(f"categories.json    : {'present' if cats.exists() else 'MISSING (numeric fallback map will be used)'}")
    print()
    print("signal types present:")
    for k, n in by_signal.most_common():
        mark = " " if k in SUPPORTED_KEYS else "  (not evaluated — needs a browser context)"
        print(f"  {k:12s} {n:6d}{mark}")
    if unsupported_only:
        print()
        print(f"note: {unsupported_only} technologies rely ONLY on js/dom signals and will never fire.")
    print()
    print("Run LakeStream with:")
    print(f"  TECH_CATALOG_PATH={path.resolve()}")
    return 0


def merge(src: Path, dest: Path) -> int:
    files = load_files(src)
    merged: dict = {}
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ! skipping {f.name}: {e}", file=sys.stderr)
            continue
        if isinstance(data, dict) and isinstance(data.get("technologies"), dict):
            data = data["technologies"]
        if isinstance(data, dict):
            merged.update(data)

    dest.mkdir(parents=True, exist_ok=True)
    (dest / "technologies.json").write_text(json.dumps(merged, indent=1))

    # Carry categories.json across when present — it is authoritative for
    # category names and avoids relying on our numeric fallback map.
    for cand in (src / "categories.json", src.parent / "categories.json"):
        if cand.exists():
            (dest / "categories.json").write_text(cand.read_text(encoding="utf-8"))
            break

    print(f"merged {len(merged)} technologies from {len(files)} files -> {dest}/technologies.json")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", required=True, type=Path,
                    help="catalog directory or a single .json file")
    ap.add_argument("--merge", type=Path, metavar="DEST",
                    help="merge all files in --path into DEST/technologies.json")
    args = ap.parse_args()

    if not args.path.exists():
        print(f"error: {args.path} does not exist", file=sys.stderr)
        return 1
    if args.merge:
        rc = merge(args.path, args.merge)
        if rc:
            return rc
        return analyse(args.merge)
    return analyse(args.path)


if __name__ == "__main__":
    raise SystemExit(main())
