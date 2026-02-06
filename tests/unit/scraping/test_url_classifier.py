from src.scraping.parser.url_classifier import classify_url, classify_urls


def test_classify_blog_url():
    result = classify_url("https://example.com/blog/my-post")
    assert result["data_type"] == "blog_url"
    assert result["confidence"] > 0.5


def test_classify_blog_insights():
    result = classify_url("https://example.com/insights/quarterly-report")
    assert result["data_type"] == "blog_url"


def test_classify_contact_url():
    result = classify_url("https://example.com/contact")
    assert result["data_type"] == "contact"


def test_classify_team_url():
    result = classify_url("https://example.com/about/team")
    assert result["data_type"] == "contact"


def test_classify_pricing_url():
    result = classify_url("https://example.com/pricing")
    assert result["data_type"] == "pricing"


def test_classify_resource_url():
    result = classify_url("https://example.com/resources/whitepapers")
    assert result["data_type"] == "resource"


def test_classify_demo_url():
    result = classify_url("https://example.com/demo")
    assert result["data_type"] == "contact"


def test_classify_date_based_article():
    result = classify_url("https://example.com/2024/01/my-article")
    assert result["data_type"] == "blog_url"


def test_classify_unknown_url():
    result = classify_url("https://example.com/some-random-page")
    assert result["data_type"] == "blog_url"  # Default
    assert result["confidence"] < 0.5


def test_classify_urls_batch():
    urls = [
        "https://example.com/blog/post-1",
        "https://example.com/contact",
        "https://example.com/pricing",
        "https://example.com/resources",
    ]
    results = classify_urls(urls)
    assert len(results) == 4
    types = [r["data_type"] for r in results]
    assert "blog_url" in types
    assert "contact" in types
    assert "pricing" in types
    assert "resource" in types
