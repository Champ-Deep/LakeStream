from src.models.template import PaginationStrategy, SelectorSet, TemplateConfig
from src.templates.base import BaseTemplate


class WordPressTemplate(BaseTemplate):
    @property
    def config(self) -> TemplateConfig:
        return TemplateConfig(
            id="wordpress",
            name="WordPress",
            description="WordPress blog and content scraper",
            platform_signals=[
                "wp-content",
                "wp-includes",
                "wordpress",
                "wp-json",
                "wp-admin",
            ],
            selectors=SelectorSet(
                blog_landing=[
                    "article.post",
                    ".blog-post",
                    ".entry",
                    ".hentry",
                    ".type-post",
                ],
                article_list=[
                    ".post-listing",
                    ".blog-listing",
                    "#main article",
                    ".posts-container",
                ],
                article_link=[
                    "a.entry-title",
                    "h2.entry-title a",
                    ".post-title a",
                    "article a[rel='bookmark']",
                    ".entry-header a",
                ],
                article_title=[
                    "h1.entry-title",
                    ".post-title",
                    "h1.wp-block-post-title",
                    ".entry-title",
                ],
                article_date=[
                    "time.entry-date",
                    ".post-date",
                    "time[datetime]",
                    ".published",
                    ".entry-date",
                ],
                article_author=[
                    ".author",
                    ".entry-author",
                    ".vcard .fn",
                    "a[rel='author']",
                    ".byline .author",
                ],
                article_content=[
                    ".entry-content",
                    ".post-content",
                    ".the-content",
                    "article .content",
                ],
                team_members=[],
                contact_info=[],
                navigation=[
                    ".nav-links",
                    ".pagination",
                    ".wp-pagenavi",
                    ".page-numbers",
                ],
            ),
            pagination=PaginationStrategy(
                type="numbered",
                next_selector="a.next.page-numbers",
                page_param_name="page",
                max_pages=50,
            ),
            blog_path_patterns=[
                r"/blog/?",
                r"/category/",
                r"/tag/",
                r"/insights/?",
                r"/news/?",
            ],
            article_path_patterns=[
                r"/\d{4}/\d{2}/",
                r"/blog/[\w-]+/?$",
            ],
            team_path_patterns=[r"/about", r"/team"],
            resource_path_patterns=[r"/resources", r"/downloads"],
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

        return list(dict.fromkeys(urls))  # deduplicate, preserve order

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

        for selector in self.config.selectors.article_date:
            node = tree.css_first(selector)
            if node:
                result["date"] = node.attributes.get("datetime") or self.clean_text(
                    node.text() or ""
                )
                break

        for selector in self.config.selectors.article_content:
            node = tree.css_first(selector)
            if node and node.text():
                text = self.clean_text(node.text())
                result["word_count"] = len(text.split())
                result["excerpt"] = text[:300]
                break

        return result

    def extract_contacts(self, html: str, url: str) -> list[dict]:
        # WordPress sites rarely have structured team data
        return []
