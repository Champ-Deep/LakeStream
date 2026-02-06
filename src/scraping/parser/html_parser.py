from urllib.parse import urljoin

from selectolax.parser import HTMLParser


class HtmlParser:
    """General-purpose HTML parser using selectolax."""

    def __init__(self, html: str, base_url: str):
        self.tree = HTMLParser(html)
        self.base_url = base_url

    def extract_title(self) -> str | None:
        """Extract page title."""
        # Try <title> tag first
        title_tag = self.tree.css_first("title")
        if title_tag and title_tag.text():
            return title_tag.text().strip()
        # Try h1
        h1 = self.tree.css_first("h1")
        if h1 and h1.text():
            return h1.text().strip()
        return None

    def extract_meta(self, name: str) -> str | None:
        """Extract a meta tag value by name or property."""
        for attr in ["name", "property"]:
            node = self.tree.css_first(f'meta[{attr}="{name}"]')
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
            for node in self.tree.css(selector):
                href = node.attributes.get("href")
                if href and not href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    absolute = urljoin(base, href)
                    urls.append(absolute)

        return list(dict.fromkeys(urls))

    def extract_text(self, selectors: list[str]) -> str | None:
        """Extract text content from the first matching selector."""
        for selector in selectors:
            node = self.tree.css_first(selector)
            if node and node.text():
                return " ".join(node.text().split()).strip()
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
            for node in self.tree.css(selector):
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
            node = self.tree.css_first(selector)
            if node and node.text():
                return len(node.text().split())
        return 0
