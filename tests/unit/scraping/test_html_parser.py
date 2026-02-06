from src.scraping.parser.html_parser import HtmlParser


def test_extract_title():
    html = "<html><head><title>My Page</title></head><body></body></html>"
    parser = HtmlParser(html, "https://example.com")
    assert parser.extract_title() == "My Page"


def test_extract_title_from_h1():
    html = "<html><body><h1>Main Heading</h1></body></html>"
    parser = HtmlParser(html, "https://example.com")
    assert parser.extract_title() == "Main Heading"


def test_extract_meta():
    html = '<html><head><meta name="author" content="John Doe"></head><body></body></html>'
    parser = HtmlParser(html, "https://example.com")
    assert parser.extract_meta("author") == "John Doe"


def test_extract_links():
    html = """
    <html><body>
    <a href="/blog/post-1">Post 1</a>
    <a href="/blog/post-2">Post 2</a>
    <a href="mailto:test@test.com">Email</a>
    </body></html>
    """
    parser = HtmlParser(html, "https://example.com")
    links = parser.extract_links()
    assert len(links) == 2
    assert "https://example.com/blog/post-1" in links
    assert "https://example.com/blog/post-2" in links


def test_extract_categories():
    html = """
    <html><body>
    <a rel="tag" href="/tag/tech">Tech</a>
    <a rel="tag" href="/tag/ai">AI</a>
    </body></html>
    """
    parser = HtmlParser(html, "https://example.com")
    cats = parser.extract_categories()
    assert "Tech" in cats
    assert "AI" in cats


def test_count_words():
    html = """
    <html><body>
    <article>This is a test article with exactly ten words in it.</article>
    </body></html>
    """
    parser = HtmlParser(html, "https://example.com")
    assert parser.count_words() == 11
