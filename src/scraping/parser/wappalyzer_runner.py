"""Wappalyzer-based technology detection alongside the curated TechParser.

Uses python-Wappalyzer with a pinned fingerprint snapshot
(src/data/wappalyzer_technologies.json, HTTPArchive/wappalyzer, ~3999 techs).
Refresh snapshot with: python scripts/update_wappalyzer_fingerprints.py
"""

import logging
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message="pkg_resources is deprecated")
try:
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except ImportError:
    pass

from Wappalyzer import Wappalyzer, WebPage  # noqa: E402

logger = logging.getLogger(__name__)

_FINGERPRINTS = Path(__file__).resolve().parents[2] / "data" / "wappalyzer_technologies.json"

_wappalyzer: Wappalyzer | None = None


def _get() -> Wappalyzer:
    global _wappalyzer
    if _wappalyzer is None:
        _wappalyzer = Wappalyzer.latest(str(_FINGERPRINTS))
    return _wappalyzer


# Wappalyzer category -> our CSV column
WAPP_CATEGORY_MAP = {
    "CMS": "platform",
    "Ecommerce": "platform",
    "Web frameworks": "frameworks",
    "JavaScript frameworks": "frameworks",
    "UI frameworks": "frameworks",
    "Mobile frameworks": "frameworks",
    "JavaScript libraries": "js_libraries",
    "Analytics": "analytics",
    "CDN": "cdn",
    "Hosting": "hosting",
    "Hosting panels": "hosting",
    "Web servers": "web_servers",
    "Operating systems": "os",
    "Programming languages": "programming_languages",
    "Widgets": "widgets",
}


def detect_wappalyzer(url: str, html: str, headers: dict | None = None) -> list[str]:
    """Return sorted technology names detected by Wappalyzer.

    Never raises: on any failure returns [] so the pipeline is unaffected.
    """
    return sorted(detect_wappalyzer_full(url, html, headers))


def detect_wappalyzer_full(url: str, html: str, headers: dict | None = None) -> dict[str, list[str]]:
    """Return {tech_name: [category names]} detected by Wappalyzer.

    Never raises: on any failure returns {} so the pipeline is unaffected.
    """
    if not html:
        return {}
    try:
        page = WebPage(url or "http://unknown", html, {k.lower(): v for k, v in (headers or {}).items()})
        raw = _get().analyze_with_categories(page)
        return {name: info["categories"] for name, info in raw.items()}
    except Exception as e:  # noqa: BLE001 - enrichment must not crash pipeline
        logger.warning("wappalyzer failed for %s: %s", url, e)
        return {}


def wapp_by_column(full: dict[str, list[str]]) -> dict[str, list[str]]:
    """Bucket categorized detections into our columns via WAPP_CATEGORY_MAP."""
    cols: dict[str, list[str]] = {}
    for name, cats in full.items():
        for cat in cats:
            col = WAPP_CATEGORY_MAP.get(cat)
            if col and name not in cols.setdefault(col, []):
                cols[col].append(name)
    return cols


def wapp_init() -> None:
    """ProcessPoolExecutor initializer: load fingerprints once per worker."""
    _get()


def wapp_worker(task: tuple[str, str, dict]) -> dict[str, list[str]]:
    """Pool worker: (url, html, headers) -> {tech_name: [categories]}."""
    url, html, headers = task
    return detect_wappalyzer_full(url, html, headers)


if __name__ == "__main__":
    # self-check: synthetic WordPress page must detect WordPress
    html = '<html><head><meta name="generator" content="WordPress 6.5"></head>' \
           '<body><script src="/wp-content/themes/x/app.js"></script></body></html>'
    res = detect_wappalyzer("http://example.com", html, {})
    assert any("WordPress" in t for t in res), f"self-check failed: {res}"
    print("self-check OK:", res)
