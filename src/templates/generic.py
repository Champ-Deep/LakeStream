from src.models.template import PaginationStrategy, SelectorSet, TemplateConfig
from src.templates.base import BaseTemplate


class GenericTemplate(BaseTemplate):
    """Fallback template for sites that don't match a specific platform."""

    @property
    def config(self) -> TemplateConfig:
        return TemplateConfig(
            id="generic",
            name="Generic",
            description="Generic fallback scraper for unrecognized platforms",
            platform_signals=[],
            selectors=SelectorSet(
                blog_landing=["article", ".post", ".blog-post", ".entry"],
                article_list=["main", "#content", ".content-area"],
                article_link=[
                    "article a",
                    "h2 a",
                    "h3 a",
                    ".post a",
                    ".entry a",
                ],
                article_title=["h1", "title", ".entry-title", ".post-title"],
                article_date=["time[datetime]", ".date", ".post-date", ".published"],
                article_author=[".author", ".byline", "[rel='author']"],
                article_content=[
                    "article",
                    ".content",
                    ".entry-content",
                    "main",
                ],
                team_members=[".team-member", ".staff", ".person", ".bio"],
                contact_info=[".contact", "address", ".vcard"],
                navigation=[".pagination", ".nav-links", "nav"],
            ),
            pagination=PaginationStrategy(
                type="next_link",
                next_selector="a[rel='next'], .next, .pagination a:last-child",
                page_param_name="page",
                max_pages=20,
            ),
            blog_path_patterns=[
                r"/blog/?",
                r"/news/?",
                r"/articles/?",
                r"/insights/?",
                r"/posts/?",
            ],
            article_path_patterns=[r"/blog/", r"/news/", r"/articles/"],
            team_path_patterns=[r"/about", r"/team", r"/people", r"/leadership"],
            resource_path_patterns=[
                r"/resources",
                r"/whitepapers",
                r"/case-studies",
                r"/webinars",
            ],
            rate_limit_ms=1500,
            max_concurrent_pages=2,
        )

    def detect_platform(self, html: str, url: str) -> bool:
        return True  # Always matches as fallback

    def extract_blog_urls(self, html: str, base_url: str) -> list[str]:
        from selectolax.parser import HTMLParser

        tree = HTMLParser(html)
        urls: list[str] = []
        for selector in self.config.selectors.article_link:
            for node in tree.css(selector):
                href = node.attributes.get("href")
                if href:
                    urls.append(self.resolve_url(href, base_url))
        return list(dict.fromkeys(urls))

    def extract_article(self, html: str, url: str) -> dict:
        from selectolax.parser import HTMLParser

        tree = HTMLParser(html)
        result: dict = {"url": url}
        for selector in self.config.selectors.article_title:
            node = tree.css_first(selector)
            if node and node.text():
                result["title"] = self.clean_text(node.text())
                break
        return result

    def extract_contacts(self, html: str, url: str) -> list[dict]:
        return []
