"""Locks the category namespace to the pinned Wappalyzer snapshot.

If someone refreshes `wappalyzer_technologies.json` and it gains categories,
these tests fail naming the new ones instead of silently pooling their
detections into "other".
"""

import json
from pathlib import Path

import pytest

from src.data.tech_signatures import TECH_SIGNATURES
from src.services.tech_categories import (
    CANONICAL_CATEGORIES,
    CANONICAL_IDS,
    WAPP_CATEGORY_TO_ID,
    group_detections,
    normalize_category,
)

_SNAPSHOT = (
    Path(__file__).resolve().parents[3] / "src" / "data" / "wappalyzer_technologies.json"
)

DELIBERATELY_OTHER = {
    "Accounting",
    "Control systems",
    "Cryptominers",
    "DMS",
    "Digital asset management",
    "Documentation",
    "Feed readers",
    "Issue trackers",
    "Miscellaneous",
    "Network devices",
    "Recruitment & staffing",
    "Remote access",
    "Webcams",
    "Webmail",
}


def _snapshot_category_names() -> set[str]:
    data = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
    return {v["name"] for v in data["categories"].values()}


def test_every_wappalyzer_category_is_mapped():
    unmapped = _snapshot_category_names() - set(WAPP_CATEGORY_TO_ID)
    assert not unmapped, f"Unmapped Wappalyzer categories: {sorted(unmapped)}"


def test_map_has_no_stale_entries():
    stale = set(WAPP_CATEGORY_TO_ID) - _snapshot_category_names()
    assert not stale, f"Mapping references categories not in the snapshot: {sorted(stale)}"


def test_every_mapping_target_is_canonical():
    bad = {v for v in WAPP_CATEGORY_TO_ID.values() if v not in CANONICAL_IDS}
    assert not bad, f"Mapping targets outside CANONICAL_IDS: {sorted(bad)}"


def test_deliberately_other_is_accurate():
    actual = {k for k, v in WAPP_CATEGORY_TO_ID.items() if v == "other"}
    assert actual == DELIBERATELY_OTHER


def test_curated_signature_categories_are_canonical():
    used = {sig["category"] for sig in TECH_SIGNATURES}
    assert used <= CANONICAL_IDS, f"Signatures use non-canonical categories: {sorted(used - CANONICAL_IDS)}"


def test_canonical_ids_are_unique_and_end_with_other():
    ids = [c for c, _ in CANONICAL_CATEGORIES]
    assert len(ids) == len(set(ids))
    assert ids[-1] == "other"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("cms", "cms"),
        ("js_library", "js_library"),
        ("Page builders", "cms"),
        ("WordPress plugins", "cms"),
        ("Databases", "database"),
        ("page builders", "cms"),
        ("platform", "cms"),
        ("marketing_tools", "marketing"),
        ("other", "other"),
        ("NotARealCategory", "other"),
        ("", "other"),
        (None, "other"),
    ],
)
def test_normalize_category(raw, expected):
    assert normalize_category(raw) == expected


def test_normalize_never_escapes_canonical_ids():
    for raw in list(_snapshot_category_names()) + ["", "junk", "other", None]:
        assert normalize_category(raw) in CANONICAL_IDS


def test_group_detections_merges_namespaces_and_dedupes():
    grouped = group_detections(
        [
            {"name": "WordPress", "category": "cms"},
            {"name": "Gutenberg", "category": "WordPress plugins"},
            {"name": "WordPress", "category": "CMS"},
            {"name": "Mystery", "category": None},
            {"name": "", "category": "cms"},
        ]
    )
    assert grouped["cms"] == ["WordPress", "Gutenberg"]
    assert grouped["other"] == ["Mystery"]
