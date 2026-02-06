from src.templates.wordpress import WordPressTemplate


def test_wordpress_detect_platform(sample_wordpress_html: str):
    template = WordPressTemplate()
    assert template.detect_platform(sample_wordpress_html, "https://example.com")


def test_wordpress_detect_platform_negative():
    template = WordPressTemplate()
    assert not template.detect_platform("<html><body>Hello</body></html>", "https://example.com")


def test_wordpress_extract_blog_urls(sample_wordpress_html: str):
    template = WordPressTemplate()
    urls = template.extract_blog_urls(sample_wordpress_html, "https://example.com")
    assert len(urls) == 2
    assert "https://example.com/blog/test-post-1" in urls
    assert "https://example.com/blog/test-post-2" in urls


def test_wordpress_config():
    template = WordPressTemplate()
    config = template.config
    assert config.id == "wordpress"
    assert config.name == "WordPress"
    assert "wp-content" in config.platform_signals
    assert config.pagination.type == "numbered"
