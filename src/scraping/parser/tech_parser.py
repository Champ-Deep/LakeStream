from src.data.tech_signatures import TECH_SIGNATURES


class TechParser:
    """Detects technology stack from HTML source and headers."""

    def __init__(self, html: str, headers: dict[str, str] | None = None):
        self.html = html.lower()
        self.headers = {k.lower(): v.lower() for k, v in (headers or {}).items()}

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
        """Check if any signal is present in HTML or headers."""
        for signal in signals:
            if signal in self.html:
                return True
            for header_val in self.headers.values():
                if signal in header_val:
                    return True
        return False
