import re

from selectolax.parser import HTMLParser

from src.data.tech_signatures import TECH_SIGNATURES


class TechParser:
    """Detects technology stack from rendered HTML, script sources, and HTTP headers.

    Since Playwright renders JS before we capture page.content(), the HTML already
    contains dynamically injected <script> tags, global window.* assignments, and
    data attributes. We scan all of these in addition to the raw HTML string.
    """

    def __init__(self, html: str, headers: dict[str, str] | None = None):
        self.raw_html = html.lower()
        self.headers = {k.lower(): v.lower() for k, v in (headers or {}).items()}

        # Build extra signal surface from parsed DOM:
        # - <script src="..."> URLs (often reveal CDN-loaded libraries)
        # - <meta name="generator"> (CMS version hints)
        # - window.__X / window.X global assignments (JS-loaded config)
        # - data-* attribute values on common elements
        tree = HTMLParser(html)
        script_srcs = [
            (node.attributes.get("src") or "").lower()
            for node in tree.css("script[src]")
        ]
        meta_generator = ""
        gen = tree.css_first('meta[name="generator"]')
        if gen:
            meta_generator = (gen.attributes.get("content") or "").lower()

        # Inline script content (window variable assignments, analytics init calls)
        inline_scripts = " ".join(
            (node.text() or "").lower()
            for node in tree.css("script:not([src])")
        )

        # Combine into one searchable blob plus individual script srcs list
        self._signal_corpus = (
            self.raw_html
            + " " + " ".join(script_srcs)
            + " " + meta_generator
            + " " + inline_scripts
        )
        self._script_srcs = script_srcs

    def detect(self) -> dict:
        """Detect technologies and return categorized results."""
        result: dict[str, list[str]] = {
            "platform": None,  # type: ignore[dict-item]
            "js_libraries": [],
            "analytics": [],
            "marketing_tools": [],
            "frameworks": [],
            "cdn": [],
        }

        for sig in TECH_SIGNATURES:
            if self._matches(sig["signals"]):
                category = sig["category"]
                name = sig["name"]

                if category == "cms":
                    # Only set platform once (first confident match wins)
                    if result["platform"] is None:
                        result["platform"] = name  # type: ignore[assignment]
                elif category == "analytics":
                    result["analytics"].append(name)
                elif category == "marketing":
                    result["marketing_tools"].append(name)
                elif category == "framework":
                    result["frameworks"].append(name)
                elif category == "cdn":
                    result["cdn"].append(name)
                elif category == "js_library":
                    result["js_libraries"].append(name)

        return result

    def _matches(self, signals: list[str]) -> bool:
        """Check if any signal is present across HTML corpus, script srcs, or headers."""
        for signal in signals:
            # Search the full signal corpus (HTML + script srcs + inline JS + meta)
            if signal in self._signal_corpus:
                return True
            # Also check each script src individually (avoids partial cross-signal matches)
            for src in self._script_srcs:
                if signal in src:
                    return True
            # Check HTTP response headers (X-Powered-By, Server, etc.)
            for header_val in self.headers.values():
                if signal in header_val:
                    return True
        return False
