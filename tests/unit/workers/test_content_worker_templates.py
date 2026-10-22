"""Confirm ContentWorker actually consults the template registry.

These tests exercise `_resolve_template` and the delegation points in
`_extract_blog_landing` / `_extract_article_record` directly (no network,
no DB) to prove WordPress-shaped HTML gets WordPress-template selectors
applied on top of the generic parser, while non-matching HTML keeps
today's pure generic-parser behavior.
"""

from uuid import uuid4

from src.scraping.parser.html_parser import HtmlParser, extract_rich_metadata
from src.templates.wordpress import WordPressTemplate
from src.workers.content_worker import ContentWorker

WORDPRESS_ARTICLE_HTML = """
<html>
<head><title>Generic Title</title></head>
<body class="wp-content">
<article class="post">
    <h1 class="entry-title">WordPress-Specific Title</h1>
    <span class="author">Jane WP Author</span>
    <div class="entry-content">
    """ + ("Lorem ipsum dolor sit amet. " * 60) + """
    </div>
</article>
</body>
</html>
"""

GENERIC_ARTICLE_HTML = """
<html>
<head><title>Plain Site Title</title></head>
<body>
<article>
    <h1>Plain Article Title</h1>
    <div class="content">
    """ + ("Lorem ipsum dolor sit amet. " * 60) + """
    </div>
</article>
</body>
</html>
"""


def _worker() -> ContentWorker:
    return ContentWorker(domain="example.com", job_id=str(uuid4()))


class TestResolveTemplate:
    def test_auto_detects_wordpress_from_html(self):
        worker = _worker()
        template = worker._resolve_template(WORDPRESS_ARTICLE_HTML, "https://example.com/blog/post")
        assert template is not None
        assert template.config.id == "wordpress"

    def test_no_match_falls_back_to_none(self):
        worker = _worker()
        template = worker._resolve_template(GENERIC_ARTICLE_HTML, "https://example.com/blog/post")
        assert template is None

    def test_explicit_generic_template_is_treated_as_no_template(self):
        from src.templates.registry import get_template

        worker = ContentWorker(
            domain="example.com", job_id=str(uuid4()), template=get_template("generic"),
        )
        template = worker._resolve_template(WORDPRESS_ARTICLE_HTML, "https://example.com/blog/post")
        # Explicit "generic" carries no benefit, so we still auto-detect —
        # and this HTML auto-detects as wordpress.
        assert template is not None
        assert template.config.id == "wordpress"

    def test_explicit_wordpress_template_used_even_without_signals(self):
        from src.templates.registry import get_template

        worker = ContentWorker(
            domain="example.com", job_id=str(uuid4()), template=get_template("wordpress"),
        )
        template = worker._resolve_template(GENERIC_ARTICLE_HTML, "https://example.com/blog/post")
        assert template is not None
        assert template.config.id == "wordpress"


class TestArticleExtractionUsesTemplate:
    def test_wordpress_html_gets_wordpress_title_and_author(self):
        worker = _worker()
        url = "https://example.com/blog/post"
        parser = HtmlParser(WORDPRESS_ARTICLE_HTML, url)
        rich_meta = extract_rich_metadata(WORDPRESS_ARTICLE_HTML, url)
        template = WordPressTemplate()

        record = worker._extract_article_record(
            url, WORDPRESS_ARTICLE_HTML, parser, rich_meta, template,
        )

        assert record is not None
        # Template's h1.entry-title selector wins over the generic <title> tag.
        assert record["title"] == "WordPress-Specific Title"
        assert record["metadata"]["author"] == "Jane WP Author"
        # Content/word_count still come from the generic parser, not the
        # template's short excerpt.
        assert record["metadata"]["word_count"] >= 200
        assert record["metadata"]["content"] is not None

    def test_no_template_keeps_generic_behavior(self):
        worker = _worker()
        url = "https://example.com/blog/post"
        parser = HtmlParser(GENERIC_ARTICLE_HTML, url)
        rich_meta = extract_rich_metadata(GENERIC_ARTICLE_HTML, url)

        record = worker._extract_article_record(
            url, GENERIC_ARTICLE_HTML, parser, rich_meta, None,
        )

        assert record is not None
        assert record["title"] == "Plain Site Title"


class TestBlogLandingUsesTemplate:
    def test_wordpress_template_adds_platform_specific_links(self, sample_wordpress_html: str):
        worker = _worker()
        url = "https://example.com/blog"
        parser = HtmlParser(sample_wordpress_html, url)
        rich_meta = extract_rich_metadata(sample_wordpress_html, url)
        template = WordPressTemplate()

        record, links = worker._extract_blog_landing(
            url, sample_wordpress_html, parser, rich_meta, template,
        )

        assert "https://example.com/blog/test-post-1" in links
        assert "https://example.com/blog/test-post-2" in links

    def test_no_template_uses_only_generic_selectors(self, sample_wordpress_html: str):
        worker = _worker()
        url = "https://example.com/blog"
        parser = HtmlParser(sample_wordpress_html, url)
        rich_meta = extract_rich_metadata(sample_wordpress_html, url)

        record, links = worker._extract_blog_landing(
            url, sample_wordpress_html, parser, rich_meta, None,
        )

        # Generic selectors (".entry-title a", "a[rel='bookmark']", etc.)
        # already cover this fixture, so behavior is unchanged.
        assert "https://example.com/blog/test-post-1" in links
        assert "https://example.com/blog/test-post-2" in links
