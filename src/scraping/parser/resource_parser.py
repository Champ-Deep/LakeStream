import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser


class ResourceParser:
    """Extracts resources (whitepapers, case studies, webinars) from HTML."""

    RESOURCE_TYPE_PATTERNS = {
        "whitepaper": [r"whitepaper", r"white\s*paper"],
        "case_study": [r"case\s*stud", r"success\s*stor"],
        "webinar": [r"webinar", r"on-demand"],
        "ebook": [r"ebook", r"e-book", r"guide"],
        "report": [r"report", r"research"],
        "infographic": [r"infographic"],
    }

    def __init__(self, html: str, base_url: str):
        self.tree = HTMLParser(html)
        self.base_url = base_url

    def extract_resources(self) -> list[dict]:
        """Extract resource items from the page."""
        resources: list[dict] = []

        # Look for resource cards/items
        for selector in [
            ".resource",
            ".resource-card",
            ".content-item",
            ".card",
            "article",
            ".download-item",
            "li",
        ]:
            items = self.tree.css(selector)
            for item in items:
                resource = self._parse_resource_item(item)
                if resource:
                    resources.append(resource)
            if resources:
                break

        # Also look for direct PDF/download links
        for link in self.tree.css('a[href$=".pdf"], a[download], a[href*="download"]'):
            href = link.attributes.get("href", "")
            if href:
                url = urljoin(self.base_url, href)
                text = link.text() or ""
                resource_type = self._detect_resource_type(text + " " + url)
                resources.append(
                    {
                        "url": url,
                        "title": text.strip() or None,
                        "resource_type": resource_type,
                        "gated": False,
                        "download_url": url,
                    }
                )

        return self._deduplicate(resources)

    def _parse_resource_item(self, node: object) -> dict | None:
        """Parse a single resource card/item."""
        # Find title
        title = None
        for sel in ["h2", "h3", "h4", ".title", "a"]:
            title_node = node.css_first(sel)  # type: ignore[attr-defined]
            if title_node and title_node.text():
                title = title_node.text().strip()
                break

        if not title or len(title) < 5:
            return None

        # Find URL
        url = self.base_url
        link = node.css_first("a")  # type: ignore[attr-defined]
        if link:
            href = link.attributes.get("href", "")
            if href:
                url = urljoin(self.base_url, href)

        # Detect resource type
        text = node.text() or ""  # type: ignore[attr-defined]
        resource_type = self._detect_resource_type(text)
        if resource_type == "unknown":
            return None  # Skip items we can't classify

        # Check if gated (has form elements)
        gated = bool(node.css_first("form, input[type='email'], .form-wrapper"))  # type: ignore[attr-defined]

        # Find download URL
        download_link = node.css_first('a[href$=".pdf"], a[download]')  # type: ignore[attr-defined]
        download_url = None
        if download_link:
            download_url = urljoin(self.base_url, download_link.attributes.get("href", ""))

        return {
            "url": url,
            "title": title,
            "resource_type": resource_type,
            "description": text[:200].strip() if text else None,
            "gated": gated,
            "download_url": download_url,
        }

    def _detect_resource_type(self, text: str) -> str:
        """Detect the type of resource from text content."""
        text_lower = text.lower()
        for rtype, patterns in self.RESOURCE_TYPE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    return rtype
        return "unknown"

    def _deduplicate(self, resources: list[dict]) -> list[dict]:
        """Remove duplicate resources by URL."""
        seen: set[str] = set()
        result: list[dict] = []
        for r in resources:
            url = r.get("url", "")
            if url not in seen:
                seen.add(url)
                result.append(r)
        return result
