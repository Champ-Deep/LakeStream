"""Characterization tests for src/workers/extractors.py.

These pin down the current (pre-refactor) behavior of the extraction
strategies that were split out of ContentWorker's private methods, so the
mechanical move (content_worker.py -> extractors.py) can be verified to have
preserved exact behavior.
"""

from uuid import uuid4

from src.models.scraped_data import DataType
from src.scraping.parser.html_parser import HtmlParser, extract_rich_metadata
from src.templates.wordpress import WordPressTemplate
from src.workers import extractors

ARTICLE_HTML = """
<html>
<head>
<title>Plain Site Title</title>
<meta name="description" content="An excerpt about the article.">
<meta name="author" content="Ada Lovelace">
</head>
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

SHORT_HTML = """
<html><head><title>Too Short</title></head>
<body><p>Not enough words here.</p></body></html>
"""

TEAM_PAGE_HTML = """
<html>
<head><title>Our Team</title></head>
<body>
<div class="team-section">
    <div class="team-member">
        <h3 class="name">Alice Johnson</h3>
        <p class="title">VP of Engineering</p>
        <a href="https://linkedin.com/in/alicejohnson">LinkedIn</a>
    </div>
    <div class="team-member">
        <h3 class="name">Bob Williams</h3>
        <p class="title">Director of Marketing</p>
        <a href="mailto:bob@example.com">Email</a>
    </div>
</div>
</body>
</html>
"""

EMAIL_ONLY_HTML = """
<html>
<head><title>Contact Us</title></head>
<body>
<p>Reach our engineering lead Jane Doe at jane.doe@example.com or call 555-123-4567.</p>
</body>
</html>
"""


def _job_id() -> str:
    return str(uuid4())


class TestExtractArticleRecord:
    def test_extracts_title_author_excerpt_word_count_content(self):
        job_id = _job_id()
        url = "https://example.com/blog/post"
        parser = HtmlParser(ARTICLE_HTML, url)
        rich_meta = extract_rich_metadata(ARTICLE_HTML, url)

        record = extractors.extract_article_record(
            job_id, "example.com", url, ARTICLE_HTML, parser, rich_meta, None,
        )

        assert record is not None
        assert record["data_type"] == DataType.ARTICLE
        assert record["url"] == url
        assert record["title"] == "Plain Site Title"
        assert record["metadata"]["author"] == "Ada Lovelace"
        assert record["metadata"]["excerpt"] == "An excerpt about the article."
        assert record["metadata"]["word_count"] >= 200
        assert "Lorem ipsum" in record["metadata"]["content"]

    def test_returns_none_when_no_words_and_no_excerpt(self):
        job_id = _job_id()
        url = "https://example.com/empty"
        empty_html = "<html><head><title>Empty</title></head><body></body></html>"
        parser = HtmlParser(empty_html, url)
        rich_meta = extract_rich_metadata(empty_html, url)

        record = extractors.extract_article_record(
            job_id, "example.com", url, empty_html, parser, rich_meta, None,
        )

        assert record is None

    def test_template_overrides_title_and_author_only(self):
        job_id = _job_id()
        url = "https://example.com/blog/post"
        parser = HtmlParser(WORDPRESS_ARTICLE_HTML, url)
        rich_meta = extract_rich_metadata(WORDPRESS_ARTICLE_HTML, url)
        template = WordPressTemplate()

        record = extractors.extract_article_record(
            job_id, "example.com", url, WORDPRESS_ARTICLE_HTML, parser, rich_meta, template,
        )

        assert record is not None
        assert record["title"] == "WordPress-Specific Title"
        assert record["metadata"]["author"] == "Jane WP Author"
        # Content/word_count always come from the generic parser, never the template.
        assert record["metadata"]["word_count"] >= 200
        assert "Lorem ipsum" in record["metadata"]["content"]

    def test_template_extract_article_failure_falls_back_to_generic(self):
        job_id = _job_id()
        url = "https://example.com/blog/post"
        parser = HtmlParser(ARTICLE_HTML, url)
        rich_meta = extract_rich_metadata(ARTICLE_HTML, url)

        class BoomTemplate:
            class config:
                id = "boom"

            def extract_article(self, html, url):
                raise RuntimeError("boom")

        record = extractors.extract_article_record(
            job_id, "example.com", url, ARTICLE_HTML, parser, rich_meta, BoomTemplate(),
        )

        assert record is not None
        assert record["title"] == "Plain Site Title"
        assert record["metadata"]["author"] == "Ada Lovelace"


class TestExtractContacts:
    def test_extracts_people_from_team_cards(self):
        job_id = _job_id()
        url = "https://example.com/team"

        records = extractors.extract_contacts(job_id, "example.com", url, TEAM_PAGE_HTML, {})

        assert len(records) == 2
        names = {r["title"] for r in records}
        assert "Alice Johnson" in names
        assert "Bob Williams" in names
        for r in records:
            assert r["data_type"] == DataType.CONTACT
            assert r["url"] == url
            assert r["job_id"] is not None
            assert r["domain"] == "example.com"

    def test_extracts_person_from_email_pattern_when_no_cards(self):
        job_id = _job_id()
        url = "https://example.com/contact"

        records = extractors.extract_contacts(job_id, "example.com", url, EMAIL_ONLY_HTML, {})

        assert len(records) >= 1
        emails = {r["metadata"]["email"] for r in records}
        assert "jane.doe@example.com" in emails

    def test_no_people_returns_empty_list(self):
        job_id = _job_id()
        url = "https://example.com/blank"
        html = "<html><body><p>Nothing to see here.</p></body></html>"

        records = extractors.extract_contacts(job_id, "example.com", url, html, {})

        assert records == []


class TestIsErrorPage:
    def test_detects_known_error_markers(self):
        assert extractors.is_error_page("404 Not Found")
        assert extractors.is_error_page("Page Not Found")
        assert extractors.is_error_page("Error")

    def test_normal_title_is_not_error_page(self):
        assert not extractors.is_error_page("Welcome to Example.com")

    def test_none_title_is_not_error_page(self):
        assert not extractors.is_error_page(None)


class TestFilterArticleLinks:
    def test_drops_homepage_offdomain_and_skipped_extensions(self):
        source_url = "https://example.com/blog"
        links = [
            "https://example.com/",  # homepage -> dropped (empty path)
            "https://example.com/blog/post-1",
            "https://other.com/blog/post-2",  # off-domain -> dropped
            "https://example.com/assets/logo.png",  # skip extension -> dropped
        ]

        filtered = extractors.filter_article_links(links, source_url)

        assert filtered == ["https://example.com/blog/post-1"]


class TestExtractBlogLanding:
    def test_generic_selectors_find_articles(self, sample_wordpress_html: str):
        job_id = _job_id()
        url = "https://example.com/blog"
        parser = HtmlParser(sample_wordpress_html, url)
        rich_meta = extract_rich_metadata(sample_wordpress_html, url)

        record, links = extractors.extract_blog_landing(
            job_id, "example.com", url, sample_wordpress_html, parser, rich_meta, None,
        )

        assert record["data_type"] == DataType.BLOG_URL
        assert "https://example.com/blog/test-post-1" in links
        assert "https://example.com/blog/test-post-2" in links
        assert record["metadata"]["total_articles"] == len(links)


class TestExtractPageRecord:
    def test_builds_page_record_with_content_and_word_count(self):
        job_id = _job_id()
        url = "https://example.com/"
        parser = HtmlParser(ARTICLE_HTML, url)
        rich_meta = extract_rich_metadata(ARTICLE_HTML, url)

        record = extractors.extract_page_record(job_id, "example.com", url, parser, rich_meta)

        assert record["data_type"] == DataType.PAGE
        assert record["metadata"]["word_count"] > 0
        assert record["title"] == "Plain Site Title"
