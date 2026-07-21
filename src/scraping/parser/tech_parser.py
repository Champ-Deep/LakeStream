"""Detects technology stack from raw HTML source code, HTTP headers, and meta tags.

Performs real source-code analysis:
  - Parses <script src="..."> URLs to identify JS libraries and frameworks
  - Parses <link href="..."> for CSS frameworks and font services
  - Reads <meta name="generator" content="..."> for CMS identification
  - Checks HTTP response headers (Server, X-Powered-By, CDN headers)
  - Scans inline <script> blocks for framework globals and init patterns
  - Substring matching on full HTML for fallback detection

Every detection carries:
  - confidence: high (meta_generator / header), medium (script/link URL), low (inline script / HTML fallback)
  - evidence: the specific string or snippet that triggered the match
  - evidence_type: which detection layer matched (meta_generator, header, script_url, link_url, inline_script, html_fallback)
"""

import re
from collections import defaultdict

from src.data.tech_signatures import TECH_SIGNATURES

# Pre-compiled regex for extracting URLs and tags from raw HTML
_RE_SCRIPT_SRC = re.compile(r'<script[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
_RE_LINK_HREF = re.compile(r'<link[^>]+href=["\']([^"\']+)["\']', re.IGNORECASE)
_RE_META_GENERATOR = re.compile(
    r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE
)
_RE_META_GENERATOR_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']generator["\']', re.IGNORECASE
)
_RE_INLINE_SCRIPT = re.compile(r'<script(?:\s[^>]*)?>(.+?)</script>', re.IGNORECASE | re.DOTALL)

# Evidence type → confidence mapping
_EVIDENCE_CONFIDENCE = {
    "meta_generator": "high",
    "header": "high",
    "script_url": "medium",
    "link_url": "medium",
    "inline_script": "medium",
    "html_fallback": "low",
}

# Category → output field mapping
_CATEGORY_FIELD = {
    "cms": "platform",
    "analytics": "analytics",
    "marketing": "marketing_tools",
    "framework": "frameworks",
    "cdn": "cdn",
    "js_library": "js_libraries",
    "hosting": "hosting",
    "backend": "backend",
    "build_tool": "build_tools",
    "font": "fonts",
    "payment": "payment",
    "auth": "auth",
    "monitoring": "monitoring",
    "search": "search",
    "ab_testing": "ab_testing",
    "tag_manager": "tag_managers",
    "video": "video",
    "ecommerce": "ecommerce",
    "a11y": "accessibility",
    "database": "databases",
}


class TechParser:
    """Detects technology stack from HTML source and HTTP headers."""

    def __init__(self, html: str, headers: dict[str, str] | None = None):
        self._raw_html = html
        self._html_lower = html.lower()
        self._headers = {k.lower(): v.lower() for k, v in (headers or {}).items()}

        # Pre-extract structured signals from HTML
        self._script_urls = [m.lower() for m in _RE_SCRIPT_SRC.findall(html)]
        self._link_urls = [m.lower() for m in _RE_LINK_HREF.findall(html)]
        self._all_asset_urls = self._script_urls + self._link_urls

        # Extract meta generator
        gen_match = _RE_META_GENERATOR.search(html) or _RE_META_GENERATOR_ALT.search(html)
        self._meta_generator = gen_match.group(1).lower().strip() if gen_match else ""

        # Extract inline script content (for framework globals detection)
        self._inline_scripts = " ".join(
            m.lower() for m in _RE_INLINE_SCRIPT.findall(html)
            if len(m) < 50000  # skip huge inline bundles
        )

    def detect(self) -> dict:
        """Detect technologies and return categorized results with evidence.

        Returns dict with:
          - Flat string/list fields for backward compatibility
          - detections: list of dicts with name, category, confidence, evidence, evidence_type
        """
        result: dict = {
            "platform": None,
            "frameworks": [],
            "js_libraries": [],
            "analytics": [],
            "marketing_tools": [],
            "cdn": [],
            "hosting": [],
            "backend": [],
            "build_tools": [],
            "fonts": [],
            "payment": [],
            "auth": [],
            "monitoring": [],
            "search": [],
            "ab_testing": [],
            "tag_managers": [],
            "video": [],
            "ecommerce": [],
            "accessibility": [],
            "databases": [],
            "detections": [],
        }

        for sig in TECH_SIGNATURES:
            match_result = self._match_with_evidence(sig)
            if not match_result:
                continue

            category = sig["category"]
            name = sig["name"]
            field = _CATEGORY_FIELD.get(category)
            evidence_type, evidence = match_result

            detection = {
                "name": name,
                "category": category,
                "confidence": _EVIDENCE_CONFIDENCE.get(evidence_type, "low"),
                "evidence": evidence,
                "evidence_type": evidence_type,
            }
            result["detections"].append(detection)

            if not field:
                continue

            if field == "platform":
                if result["platform"] is None:
                    result["platform"] = name
            else:
                lst = result[field]
                if isinstance(lst, list) and name not in lst:
                    lst.append(name)

        return result

    def _match_with_evidence(self, sig: dict) -> tuple[str, str] | None:
        """Check signature against all detection layers.

        Returns (evidence_type, evidence_string) on match, None otherwise.
        Layers are checked in order of decreasing confidence.
        """
        # Layer 1: meta generator (most authoritative for CMS)
        meta_gen = sig.get("meta_generator")
        if meta_gen and meta_gen in self._meta_generator:
            return ("meta_generator", self._meta_generator)

        # Layer 2: header-specific signals (X-Powered-By, Server, CDN headers)
        header_result = self._match_headers_with_evidence(sig.get("header_signals", []))
        if header_result:
            return ("header", header_result)

        signals = sig.get("signals", [])

        # Layer 3: script/link URL matching (most reliable for JS detection)
        for signal in signals:
            for asset_url in self._script_urls:
                if signal in asset_url:
                    return ("script_url", asset_url)
            for asset_url in self._link_urls:
                if signal in asset_url:
                    return ("link_url", asset_url)

        # Layer 4: inline script content (framework globals like __next_data__)
        for signal in signals:
            if signal in self._inline_scripts:
                return ("inline_script", signal)

        # Layer 5: full HTML substring fallback (broadest, lowest confidence)
        for signal in signals:
            if signal in self._html_lower:
                return ("html_fallback", signal)

        return None

    def _match_headers_with_evidence(self, header_signals: list[str]) -> str | None:
        """Match against HTTP response headers, return the evidence string."""
        for signal in header_signals:
            sig_lower = signal.lower()
            if ": " in sig_lower:
                key, value = sig_lower.split(": ", 1)
                header_val = self._headers.get(key, "")
                if value in header_val:
                    return f"{key}: {header_val}"
            else:
                for hk, hv in self._headers.items():
                    if sig_lower in hk or sig_lower in hv:
                        return f"{hk}: {hv}"
        return None

    def get_script_urls(self) -> list[str]:
        """Return all extracted <script src> URLs (useful for deeper analysis)."""
        return self._script_urls

    def get_link_urls(self) -> list[str]:
        """Return all extracted <link href> URLs."""
        return self._link_urls

    def get_meta_generator(self) -> str:
        """Return the <meta name='generator'> value if present."""
        return self._meta_generator
