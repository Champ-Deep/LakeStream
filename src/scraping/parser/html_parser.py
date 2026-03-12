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

    def extract_content(self, max_chars: int = 50_000) -> str | None:
        """Extract main article/page body text."""
        for selector in [
            # Common WordPress/blog patterns
            ".entry-content",
            ".post-content",
            ".post-body",
            ".article-content",
            ".blog-content",
            # Semantic HTML5
            "article",
            "main",
            # Generic content containers
            "#left_content",  # fonada.com and similar sites
            "#content",
            ".content",
            "#main-content",
            ".main-content",
            # Fallback: any div with substantial text
            "body",
        ]:
            node = self.tree.css_first(selector)
            if node and node.text():
                text = " ".join(node.text().split()).strip()
                if len(text) > 100:  # Skip trivially short matches
                    return text[:max_chars]
        return None

    def count_words(self) -> int:
        """Count words in main content."""
        content = self.extract_content()
        return len(content.split()) if content else 0


def extract_rich_metadata(html: str, url: str = "") -> dict:
    """
    Extract rich metadata (og:, twitter:, meta: tags) for B2B enrichment.

    Returns dict with keys:
    - title: Page title
    - description: Meta description
    - og_title, og_description, og_image, og_url, og_type, og_site_name
    - twitter_card, twitter_site, twitter_creator, twitter_title, twitter_description, twitter_image
    - favicon: Favicon URL
    - canonical_url: Canonical URL
    """
    parser = HTMLParser(html)
    metadata = {}

    # Title
    title_tag = parser.css_first("title")
    if title_tag:
        metadata["title"] = title_tag.text().strip()

    # Meta tags
    for meta in parser.css("meta"):
        attrs = meta.attributes

        # og:* tags (Open Graph)
        if "property" in attrs and attrs["property"].startswith("og:"):
            key = attrs["property"].replace("og:", "").replace(":", "_")
            metadata[f"og_{key}"] = attrs.get("content", "")

        # twitter:* tags
        elif "name" in attrs and attrs["name"].startswith("twitter:"):
            key = attrs["name"].replace("twitter:", "").replace(":", "_")
            metadata[f"twitter_{key}"] = attrs.get("content", "")

        # Standard meta tags
        elif "name" in attrs:
            if attrs["name"] == "description":
                metadata["description"] = attrs.get("content", "")
            elif attrs["name"] == "keywords":
                metadata["keywords"] = attrs.get("content", "")
            elif attrs["name"] == "author":
                metadata["author"] = attrs.get("content", "")

    # Favicon (prefer PNG/SVG, fallback to ICO)
    favicon = (
        parser.css_first("link[rel='icon'][type='image/png']")
        or parser.css_first("link[rel='icon'][type='image/svg+xml']")
        or parser.css_first("link[rel='icon']")
        or parser.css_first("link[rel='shortcut icon']")
    )
    if favicon and "href" in favicon.attributes:
        favicon_url = favicon.attributes["href"]
        # Make absolute URL
        if url and not favicon_url.startswith("http"):
            favicon_url = urljoin(url, favicon_url)
        metadata["favicon"] = favicon_url

    # Canonical URL
    canonical = parser.css_first("link[rel='canonical']")
    if canonical and "href" in canonical.attributes:
        metadata["canonical_url"] = canonical.attributes["href"]

    # Clean empty strings
    return {k: v for k, v in metadata.items() if v}
