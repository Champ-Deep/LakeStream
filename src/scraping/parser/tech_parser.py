"""Detects a site's technology stack from HTML, response headers, and cookies.

Multi-signal regex engine (see src/data/tech_signatures.py for the fingerprint
database): each signature is scoped to "html", "header", or "cookie", and
header-scoped signatures may further restrict to one named header (e.g. only
the `Server` header for web-server detection, to avoid false positives from
unrelated header values containing the same word).
"""

import re

from src.data.tech_signatures import TECH_SIGNATURES

# category -> plural output key on TechStackMetadata
_LIST_CATEGORIES = {
    "analytics": "analytics",
    "marketing": "marketing_tools",
    "framework": "frameworks",
    "cdn": "cdn",
    "js_library": "js_libraries",
    "widget": "widgets",
    "web_server": "web_servers",
    "programming_language": "programming_languages",
}


class TechParser:
    """Detects technology stack from HTML source, response headers, and cookies."""

    def __init__(self, html: str, headers: dict[str, str] | None = None):
        self.html = html or ""
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.cookie_blob = self.headers.get("set-cookie", "")

    def detect(self) -> dict:
        """Detect technologies and return categorized results."""
        result: dict = {
            "platform": None,
            "js_libraries": [],
            "analytics": [],
            "marketing_tools": [],
            "frameworks": [],
            "cdn": [],
            "widgets": [],
            "web_servers": [],
            "programming_languages": [],
            "server_os": None,
        }

        for sig in TECH_SIGNATURES:
            if not self._matches(sig):
                continue
            category = sig["category"]
            name = sig["name"]

            if category == "cms":
                result["platform"] = result["platform"] or name
            elif category == "os":
                result["server_os"] = result["server_os"] or name
            else:
                key = _LIST_CATEGORIES.get(category)
                if key and name not in result[key]:
                    result[key].append(name)

        return result

    def _matches(self, sig: dict) -> bool:
        # Default "any": check the HTML body plus every header's name/value.
        # This matches a URL fragment like "cloudfront.net" in a <script src>
        # just as readily as a bare header key like "cf-ray". Signatures that
        # need precision (a specific header only, or a cookie only) opt into
        # a narrower explicit scope below to avoid false positives.
        scope = sig.get("scope", "any")
        patterns = [re.compile(p, re.IGNORECASE) for p in sig["signals"]]

        if scope == "cookie":
            return any(p.search(self.cookie_blob) for p in patterns)

        if scope == "header":
            header_name = sig.get("header_name")
            if header_name:
                value = self.headers.get(header_name, "")
                return any(p.search(value) for p in patterns)
            return any(
                p.search(k) or p.search(v)
                for p in patterns
                for k, v in self.headers.items()
            )

        if scope == "html":
            return any(p.search(self.html) for p in patterns)

        # scope == "any"
        if any(p.search(self.html) for p in patterns):
            return True
        return any(
            p.search(k) or p.search(v)
            for p in patterns
            for k, v in self.headers.items()
        )
