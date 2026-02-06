from src.models.template import PaginationStrategy, SelectorSet, TemplateConfig
from src.templates.base import BaseTemplate


class WebflowTemplate(BaseTemplate):
    @property
    def config(self) -> TemplateConfig:
        return TemplateConfig(
            id="webflow",
            name="Webflow",
            description="Webflow marketing site and blog scraper",
            platform_signals=["webflow.com", "wf-page", "wf-section", "w-dyn-list"],
            selectors=SelectorSet(
                blog_landing=[".w-dyn-list", ".collection-list"],
                article_list=[".w-dyn-items", ".collection-list-wrapper"],
                article_link=[
                    ".w-dyn-item a",
                    ".collection-item a",
                    ".blog-link",
                ],
                article_title=["h1", ".blog-title", ".post-title"],
                article_date=[".post-date", ".blog-date", "time"],
                article_author=[".author", ".post-author"],
                article_content=[".blog-content", ".post-body", ".rich-text-block"],
                team_members=[".team-member", ".w-dyn-item"],
                contact_info=[".contact-form", "form"],
                navigation=[".w-pagination", ".pagination"],
            ),
            pagination=PaginationStrategy(
                type="next_link",
                next_selector=".w-pagination-next",
                page_param_name=None,
                max_pages=20,
            ),
            blog_path_patterns=[r"/blog/?", r"/posts/?", r"/articles/?"],
            article_path_patterns=[r"/blog/", r"/posts/"],
            team_path_patterns=[r"/about", r"/team"],
            resource_path_patterns=[r"/resources", r"/library"],
            rate_limit_ms=1000,
            max_concurrent_pages=3,
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
        return result

    def extract_contacts(self, html: str, url: str) -> list[dict]:
        return []
