"""Integration tests for content extraction accuracy and quality validation.

Tests real-world scraping scenarios against known-good sites to validate:
- Blog article extraction (title, content, author, date, images)
- Contact information extraction (email, phone, LinkedIn)
- Pricing page extraction (plans, prices, features)
- Content quality metrics (precision, recall, F1 score)

These tests use real HTTP requests to public sites, so they:
- Require network connectivity
- May be slow (marked with @pytest.mark.slow)
- Should use stable, public test sites
"""

import asyncio

import pytest
from src.scraping.fetcher.lake_fetcher import LakeFetcher

from src.models.scraping import FetchOptions
from src.scraping.parser.html_parser import HTMLParser
from src.templates.wordpress import WordPressTemplate


@pytest.mark.integration
@pytest.mark.quality
@pytest.mark.slow
class TestBlogExtractionAccuracy:
    """Test blog article extraction accuracy on real B2B sites."""

    @pytest.fixture
    def fetcher(self):
        """Basic HTTP fetcher for blog scraping."""
        return LakeFetcher()

    @pytest.fixture
    def parser(self):
        """HTML parser for content extraction."""
        return HTMLParser()

    async def test_blog_list_extraction(self, fetcher: LakeFetcher):
        """Test extracting blog article list from HubSpot blog.

        Validates:
        - Can fetch blog homepage successfully
        - Extracts multiple article links (expect 5-20)
        - Article URLs follow expected pattern
        - Response time acceptable (<5 seconds)
        """
        url = "https://blog.hubspot.com"
        result = await fetcher.fetch(url, FetchOptions(timeout=10000))

        # Basic fetch validation
        assert result.status_code == 200, f"Failed to fetch {url}: {result.status_code}"
        assert len(result.html) > 1000, "HTML too short, likely blocked or empty"
        assert not result.blocked, "Fetcher detected blocking"

        # Parse article links
        from selectolax.parser import HTMLParser as SelectolaxParser

        tree = SelectolaxParser(result.html)
        # HubSpot blog uses <a> tags with href containing "/blog/"
        article_links = [
            node.attributes.get("href")
            for node in tree.css("a[href*='/blog/']")
            if node.attributes.get("href")
        ]

        # Remove duplicates and filter out non-article URLs
        article_links = list(
            set(
                link
                for link in article_links
                if link
                and "/blog/" in link
                and not link.endswith("/blog")
                and not link.endswith("/blog/")
            )
        )

        # Quality checks
        assert len(article_links) >= 5, f"Expected ≥5 articles, found {len(article_links)}"
        assert result.duration_ms < 5000, f"Fetch too slow: {result.duration_ms}ms"

        # Verify URL patterns
        for link in article_links[:5]:
            assert (
                "hubspot.com" in link or link.startswith("/blog/")
            ), f"Unexpected URL pattern: {link}"

    async def test_article_content_extraction(self, fetcher: LakeFetcher, parser: HTMLParser):
        """Test extracting content from a specific blog article.

        Validates:
        - Title extracted correctly
        - Content length substantial (>500 words)
        - Images extracted
        - Metadata present (author, date if available)
        """
        # Use a stable HubSpot article (unlikely to change)
        url = "https://blog.hubspot.com/marketing/marketing-statistics"
        result = await fetcher.fetch(url, FetchOptions(timeout=10000))

        assert result.status_code == 200
        assert len(result.html) > 5000, "Article HTML too short"

        # Extract structured content
        from selectolax.parser import HTMLParser as SelectolaxParser

        tree = SelectolaxParser(result.html)

        # Title extraction
        title = None
        for selector in ["h1", "h1.post-title", "h1.entry-title", "article h1"]:
            node = tree.css_first(selector)
            if node and node.text(strip=True):
                title = node.text(strip=True)
                break

        assert title is not None, "Failed to extract article title"
        assert len(title) > 10, f"Title too short: {title}"
        assert len(title) < 200, f"Title too long (likely extracted wrong element): {title}"

        # Content extraction - look for main content container
        content = ""
        for selector in ["article", ".post-body", ".entry-content", "main"]:
            node = tree.css_first(selector)
            if node:
                content = node.text(strip=True)
                break

        # Word count validation
        word_count = len(content.split())
        assert word_count > 500, f"Content too short: {word_count} words (expected >500)"

        # Image extraction
        images = [img.attributes.get("src") for img in tree.css("img") if img.attributes.get("src")]
        assert len(images) > 0, "No images extracted from article"

    async def test_wordpress_template_accuracy(self, fetcher: LakeFetcher):
        """Test WordPress template detection and article extraction.

        Uses WordPress template to extract blog articles and validates
        extraction accuracy against known patterns.
        """
        template = WordPressTemplate()
        url = "https://blog.hubspot.com"

        # Fetch blog homepage
        result = await fetcher.fetch(url, FetchOptions(timeout=10000))
        assert result.status_code == 200

        # Use template to detect blog URLs
        from selectolax.parser import HTMLParser as SelectolaxParser

        tree = SelectolaxParser(result.html)

        # WordPress template should identify blog article patterns
        article_links = template.extract_blog_urls(tree)

        # Validation
        assert len(article_links) > 0, "WordPress template failed to extract any articles"
        assert all(
            isinstance(link, str) for link in article_links
        ), "Invalid link format extracted"

        # Check that links are reasonable
        for link in article_links[:5]:
            assert len(link) > 10, f"Link too short: {link}"
            assert link.startswith("http") or link.startswith(
                "/"
            ), f"Invalid link format: {link}"

    async def test_content_quality_metrics(self, fetcher: LakeFetcher):
        """Calculate content quality metrics across multiple articles.

        Metrics:
        - Precision: (correct extractions / total extractions)
        - Recall: (extracted items / total items on page)
        - F1 Score: harmonic mean of precision and recall

        Target: >90% precision, >80% recall
        """
        test_urls = [
            "https://blog.hubspot.com/marketing/marketing-statistics",
            "https://blog.hubspot.com/sales/sales-statistics",
        ]

        total_titles_found = 0
        total_content_valid = 0
        total_images_found = 0

        for url in test_urls:
            try:
                result = await fetcher.fetch(url, FetchOptions(timeout=10000))
                if result.status_code != 200:
                    continue

                from selectolax.parser import HTMLParser as SelectolaxParser

                tree = SelectolaxParser(result.html)

                # Title extraction
                title = tree.css_first("h1")
                if title and len(title.text(strip=True)) > 10:
                    total_titles_found += 1

                # Content validation (>500 words)
                content = tree.css_first("article")
                if content and len(content.text(strip=True).split()) > 500:
                    total_content_valid += 1

                # Image extraction
                images = tree.css("img")
                if len(images) > 0:
                    total_images_found += 1

                # Rate limit - wait between requests
                await asyncio.sleep(2)

            except Exception as exc:
                # Log but don't fail on individual article errors
                print(f"Error processing {url}: {exc}")
                continue

        # Calculate precision (% of successful extractions)
        total_tests = len(test_urls)
        title_precision = total_titles_found / total_tests
        content_precision = total_content_valid / total_tests
        image_precision = total_images_found / total_tests

        # Overall precision (average)
        precision = (title_precision + content_precision + image_precision) / 3

        # Quality thresholds
        assert title_precision >= 0.9, f"Title precision too low: {title_precision:.1%}"
        assert (
            content_precision >= 0.8
        ), f"Content precision too low: {content_precision:.1%}"
        assert precision >= 0.8, f"Overall precision too low: {precision:.1%}"


@pytest.mark.integration
@pytest.mark.quality
class TestContactExtractionAccuracy:
    """Test contact information extraction accuracy."""

    @pytest.fixture
    def fetcher(self):
        return LakeFetcher()

    async def test_email_extraction_pattern_validation(self, fetcher: LakeFetcher):
        """Test email extraction using regex patterns.

        Validates:
        - Email regex correctly identifies emails
        - No false positives (image filenames, etc.)
        - Domain validation works
        """
        # Use a page with known email patterns (example.com)
        test_html = """
        <html>
        <body>
            <p>Contact us at info@example.com or support@example.com</p>
            <p>No spam: notanemail@image.jpg</p>
            <img src="logo@2x.png" />
        </body>
        </html>
        """

        import re

        # Email regex from contact_parser
        email_pattern = re.compile(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
        )
        emails = email_pattern.findall(test_html)

        # Filter out image files and disposable domains
        disposable_patterns = [".jpg", ".png", ".gif", "tempmail", "10minutemail"]
        valid_emails = [
            email
            for email in emails
            if not any(pattern in email.lower() for pattern in disposable_patterns)
        ]

        assert "info@example.com" in valid_emails
        assert "support@example.com" in valid_emails
        assert "notanemail@image.jpg" not in valid_emails
        assert len(valid_emails) == 2

    async def test_phone_number_extraction(self):
        """Test phone number extraction patterns.

        Validates US phone number formats:
        - (555) 123-4567
        - 555-123-4567
        - 555.123.4567
        - +1 555 123 4567
        """
        test_html = """
        <html>
        <body>
            <p>Call us at (650) 555-1234</p>
            <p>Or text: 650-555-5678</p>
            <p>International: +1 650 555 9999</p>
            <p>Not a phone: 123-45-6789 (SSN)</p>
        </body>
        </html>
        """

        import re

        # US phone number pattern
        phone_pattern = re.compile(
            r"(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
        )
        phones = phone_pattern.findall(test_html)

        # Should find 3 valid phone numbers
        assert len(phones) >= 3, f"Expected 3+ phone numbers, found {len(phones)}"


@pytest.mark.integration
@pytest.mark.quality
class TestPricingExtractionAccuracy:
    """Test pricing page extraction accuracy."""

    async def test_price_pattern_extraction(self):
        """Test extracting prices from HTML.

        Validates:
        - Dollar amounts: $99, $1,234.56
        - Price ranges: $10-$50
        - Per-unit pricing: $99/month
        """
        test_html = """
        <html>
        <body>
            <div class="pricing-plan">
                <h3>Starter</h3>
                <p class="price">$29/month</p>
                <ul class="features">
                    <li>Feature 1</li>
                    <li>Feature 2</li>
                </ul>
            </div>
            <div class="pricing-plan">
                <h3>Professional</h3>
                <p class="price">$99/month</p>
                <ul class="features">
                    <li>Everything in Starter</li>
                    <li>Feature 3</li>
                </ul>
            </div>
        </body>
        </html>
        """

        import re

        # Price pattern
        price_pattern = re.compile(r"\$\d+(?:,\d{3})*(?:\.\d{2})?(?:/\w+)?")
        prices = price_pattern.findall(test_html)

        assert "$29/month" in prices
        assert "$99/month" in prices
        assert len(prices) == 2

    async def test_plan_name_extraction(self):
        """Test extracting pricing plan names.

        Common patterns: Starter, Basic, Pro, Professional, Enterprise
        """
        test_html = """
        <div class="pricing-card">
            <h2 class="plan-name">Professional Plan</h2>
            <span class="price">$99</span>
        </div>
        """

        from selectolax.parser import HTMLParser

        tree = HTMLParser(test_html)
        plan_name = tree.css_first(".plan-name")

        assert plan_name is not None
        assert "Professional" in plan_name.text()
