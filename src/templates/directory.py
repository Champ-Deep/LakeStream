from src.models.template import PaginationStrategy, SelectorSet, TemplateConfig
from src.templates.base import BaseTemplate


class DirectoryTemplate(BaseTemplate):
    """Template for directory/listing style pages."""

    @property
    def config(self) -> TemplateConfig:
        return TemplateConfig(
            id="directory",
            name="Directory",
            description="Directory and listing page scraper",
            platform_signals=[],
            selectors=SelectorSet(
                blog_landing=[],
                article_list=[
                    ".directory-list",
                    ".listing",
                    "table",
                    ".results",
                    "ul.list",
                ],
                article_link=[
                    ".listing a",
                    ".directory-item a",
                    "table a",
                    ".result a",
                ],
                article_title=["h1", ".page-title"],
                article_date=[],
                article_author=[],
                article_content=[],
                team_members=[
                    ".person",
                    ".profile",
                    ".member",
                    ".team-member",
                    "tr",
                ],
                contact_info=[".contact", ".email", ".phone"],
                navigation=[".pagination", ".pager", "nav.pages"],
            ),
            pagination=PaginationStrategy(
                type="numbered",
                next_selector=".next, a[rel='next']",
                page_param_name="page",
                max_pages=100,
            ),
            blog_path_patterns=[],
            article_path_patterns=[],
            team_path_patterns=[r"/directory", r"/people", r"/members"],
            resource_path_patterns=[],
            rate_limit_ms=2000,
            max_concurrent_pages=2,
        )

    def detect_platform(self, html: str, url: str) -> bool:
        return False  # Must be explicitly selected

    def extract_blog_urls(self, html: str, base_url: str) -> list[str]:
        return []

    def extract_article(self, html: str, url: str) -> dict:
        return {"url": url}

    def extract_contacts(self, html: str, url: str) -> list[dict]:
        return []
