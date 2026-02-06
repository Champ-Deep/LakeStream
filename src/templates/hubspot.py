from src.models.template import PaginationStrategy, SelectorSet, TemplateConfig
from src.templates.base import BaseTemplate


class HubSpotTemplate(BaseTemplate):
    @property
    def config(self) -> TemplateConfig:
        return TemplateConfig(
            id="hubspot",
            name="HubSpot",
            description="HubSpot CMS blog and resource center scraper",
            platform_signals=[
                "js.hs-scripts.com",
                "hs-script-loader",
                "hubspot",
                ".hs-",
                "hbspt",
            ],
            selectors=SelectorSet(
                blog_landing=[".blog-listing", ".hs-blog-listing", ".post-listing"],
                article_list=[".blog-listing-wrapper", ".content-wrapper"],
                article_link=[
                    ".blog-listing a",
                    ".hs-blog-post a",
                    ".post-listing-wrapper a",
                    "a.blog-post-link",
                ],
                article_title=["h1", ".blog-post-title", ".hs-blog-post-title"],
                article_date=[".post-date", ".blog-post-date", "time[datetime]"],
                article_author=[".author-name", ".blog-post-author", ".hs-author-name"],
                article_content=[".blog-post-body", ".post-body", ".hs-blog-post-body"],
                team_members=[".team-member", ".staff-card"],
                contact_info=[".contact-form", ".hs-form"],
                navigation=[".blog-pagination", ".pagination"],
            ),
            pagination=PaginationStrategy(
                type="numbered",
                next_selector=".blog-pagination a.next",
                page_param_name=None,
                max_pages=30,
            ),
            blog_path_patterns=[r"/blog/?", r"/resources/?", r"/knowledge/?"],
            article_path_patterns=[r"/blog/"],
            team_path_patterns=[r"/about", r"/team", r"/company"],
            resource_path_patterns=[r"/resources", r"/library", r"/ebooks"],
            rate_limit_ms=1500,
            max_concurrent_pages=2,
        )

    def detect_platform(self, html: str, url: str) -> bool:
        html_lower = html.lower()
        return any(signal in html_lower for signal in self.config.platform_signals)

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

        for selector in self.config.selectors.article_author:
            node = tree.css_first(selector)
            if node and node.text():
                result["author"] = self.clean_text(node.text())
                break

        return result

    def extract_contacts(self, html: str, url: str) -> list[dict]:
        return []
