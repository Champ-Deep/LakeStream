"""GPL guardrail: no third-party fingerprint catalog may enter this repo.

The enthec/webappanalyzer ruleset (the maintained Wappalyzer continuation) is
GPL-3.0. LakeStream is distributed to clients, so that data is used strictly
as an out-of-repo coverage reference (scripts/tech_gap_analysis.py downloads
it to the gitignored .tech-reference/). This test fails the build if catalog
data — or a default configuration that loads one — sneaks into the tree.
"""

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]

# Wappalyzer-format catalog entries are recognizable by this field combination
# inside a single JSON object; no legitimate LakeStream file has it.
_DATASET_FIELDS = ("cats", "scriptSrc")


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return [REPO_ROOT / line for line in out.stdout.splitlines() if line]


def test_no_wappalyzer_format_json_is_tracked():
    offenders = []
    for path in _tracked_files():
        if path.suffix != ".json":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if all(f'"{field}"' in text for field in _DATASET_FIELDS):
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and any(
                isinstance(v, dict) and "cats" in v for v in data.values()
            ):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        f"Wappalyzer-format catalog data is tracked in the repo: {offenders}. "
        "That data is GPL-3.0 — keep it in the gitignored .tech-reference/ "
        "and author signatures natively (docs/adding-tech-signatures.md)."
    )


def test_reference_directory_is_gitignored():
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    assert ".tech-reference/" in gitignore


def test_default_settings_load_no_external_catalog():
    """The shipped default must never point at an external catalog file."""
    from src.config.settings import Settings

    field = Settings.model_fields["tech_catalog_path"]
    assert field.default == ""


def test_nothing_tracked_under_tech_reference():
    tracked = subprocess.run(
        ["git", "ls-files", ".tech-reference"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    assert tracked.stdout.strip() == ""
