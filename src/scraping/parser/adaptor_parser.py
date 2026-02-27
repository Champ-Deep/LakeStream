from urllib.parse import urljoin

from scrapling.parser import Selector


class AdaptorParser:
    """Adaptive HTML parser with intelligent element finding.

    This is an alternative to selectolax-based HtmlParser.
    Provides resilient element selection that survives site redesigns.
    """

    def __init__(self, html: str, base_url: str):
        self.parser = Selector(html)
        self.base_url = base_url

    def extract_title(self) -> str | None:
        """Extract page title."""
        title_tag = self.parser.css_first("title")
        if title_tag:
            text = title_tag.text()
            if text:
                return text.strip()
        h1 = self.parser.css_first("h1")
        if h1:
            text = h1.text()
            if text:
                return text.strip()
        return None

    def extract_meta(self, name: str) -> str | None:
        """Extract a meta tag value by name or property."""
        for attr in ["name", "property"]:
            node = self.parser.css_first(f'meta[{attr}="{name}"]')
            if node:
                content = node.attributes.get("content")
                if content:
                    return content.strip()
        return None

    def extract_links(
        self,
        selectors: list[str] | None = None,
        base_url: str | None = None,
    ) -> list[str]:
        """Extract links matching CSS selectors."""
        base = base_url or self.base_url
        selectors = selectors or ["a[href]"]
        urls: list[str] = []

        for selector in selectors:
            nodes = self.parser.css(selector)
            for node in nodes:
                href = node.attributes.get("href")
                if href and not href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    absolute = urljoin(base, href)
                    urls.append(absolute)

        return list(dict.fromkeys(urls))

    def extract_text(self, selectors: list[str]) -> str | None:
        """Extract text content from the first matching selector."""
        for selector in selectors:
            node = self.parser.css_first(selector)
            if node:
                text = node.text()
                if text:
                    return " ".join(text.split()).strip()
        return None

    def extract_categories(self) -> list[str]:
        """Extract article categories/tags."""
        categories: list[str] = []
        for selector in [
            "a[rel='tag']",
            ".category a",
            ".tag a",
            ".post-categories a",
            ".entry-categories a",
        ]:
            nodes = self.parser.css(selector)
            for node in nodes:
                text = node.text()
                if text:
                    categories.append(text.strip())
        return list(dict.fromkeys(categories))

    def count_words(self) -> int:
        """Count words in main content."""
        for selector in [
            ".entry-content",
            ".post-content",
            "article",
            "main",
            ".content",
        ]:
            node = self.parser.css_first(selector)
            if node:
                text = node.text()
                if text:
                    return len(text.split())
        return 0

    def css(self, selector: str):
        """Direct CSS selector access via Adaptor."""
        return self.parser.css(selector)

    def css_first(self, selector: str):
        """Direct CSS selector access (first match) via Adaptor."""
        return self.parser.css_first(selector)

    def xpath(self, selector: str):
        """Direct XPath selector access via Adaptor."""
        return self.parser.xpath(selector)

    def find_by_text(self, text: str, **kwargs):
        """Find elements by text content."""
        return self.parser.find_by_text(text, **kwargs)
