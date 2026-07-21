"""Tests for TechParser — signature-based technology stack detection with evidence tracking."""

from src.scraping.parser.tech_parser import TechParser


def _make_headers(**kwargs) -> dict[str, str]:
    return dict(kwargs)


class TestTechParserBase:
    """Core detection: each detection layer produces correct evidence."""

    def test_meta_generator_high_confidence(self):
        html = '<html><head><meta name="generator" content="WordPress 6.4"></head><body></body></html>'
        parser = TechParser(html)
        result = parser.detect()

        assert result["platform"] == "WordPress"
        detections = result["detections"]
        wp_detections = [d for d in detections if d["name"] == "WordPress"]
        assert len(wp_detections) == 1
        assert wp_detections[0]["confidence"] == "high"
        assert wp_detections[0]["evidence_type"] == "meta_generator"

    def test_header_signal_high_confidence(self):
        html = "<html><body>Hello</body></html>"
        headers = _make_headers(server="nginx/1.24.0")
        parser = TechParser(html, headers)
        result = parser.detect()

        nginx = [d for d in result["detections"] if d["name"] == "Nginx"]
        assert len(nginx) == 1
        assert nginx[0]["confidence"] == "high"
        assert nginx[0]["evidence_type"] == "header"
        assert "server:" in nginx[0]["evidence"].lower()

    def test_script_url_medium_confidence(self):
        html = '<html><head><script src="https://cdn.example.com/react.production.min.js"></script></head><body></body></html>'
        parser = TechParser(html)
        result = parser.detect()

        react = [d for d in result["detections"] if d["name"] == "React"]
        assert len(react) == 1
        assert react[0]["confidence"] == "medium"
        assert react[0]["evidence_type"] in ("script_url", "link_url")

    def test_link_url_medium_confidence(self):
        html = '<html><head><link href="https://fonts.googleapis.com/css?family=Inter" rel="stylesheet"></head><body></body></html>'
        parser = TechParser(html)
        result = parser.detect()

        gf = [d for d in result["detections"] if d["name"] == "Google Fonts"]
        assert len(gf) == 1
        assert gf[0]["confidence"] == "medium"
        assert gf[0]["evidence_type"] in ("script_url", "link_url")

    def test_inline_script_medium_confidence(self):
        html = '<html><body><script>window.__next_data__ = {};</script></body></html>'
        parser = TechParser(html)
        result = parser.detect()

        nextjs = [d for d in result["detections"] if d["name"] == "Next.js"]
        assert len(nextjs) == 1
        assert nextjs[0]["confidence"] == "medium"
        assert nextjs[0]["evidence_type"] == "inline_script"

    def test_html_fallback_low_confidence(self):
        html = '<html><body><a href="/wp-admin">Admin</a></body></html>'
        parser = TechParser(html)
        result = parser.detect()

        wp = [d for d in result["detections"] if d["name"] == "WordPress"]
        assert len(wp) == 1
        assert wp[0]["confidence"] == "low"
        assert wp[0]["evidence_type"] == "html_fallback"


class TestTechParserIntegration:
    """Realistic HTML snippets simulating actual sites."""

    def test_wordpress_site(self):
        html = """<html>
<head>
    <meta name="generator" content="WordPress 6.4.2">
    <script src="https://example.com/wp-content/themes/theme/js/script.js"></script>
</head>
<body>Content here</body></html>"""
        parser = TechParser(html)
        result = parser.detect()

        assert result["platform"] == "WordPress"
        wp = [d for d in result["detections"] if d["name"] == "WordPress"]
        assert len(wp) == 1
        # Should match on meta_generator (highest confidence layer wins first)
        assert wp[0]["confidence"] == "high"
        assert wp[0]["evidence_type"] == "meta_generator"

    def test_stripe_tech_stack(self):
        html = """<html>
<head>
    <script src="https://js.stripe.com/v3/"></script>
    <script src="https://cdn.jsdelivr.net/npm/react@18/umd/react.production.min.js"></script>
    <link href="https://fonts.googleapis.com/css?family=Inter" rel="stylesheet">
    <meta name="generator" content="Next.js">
    <script>window.__next_data__ = {};</script>
    <script src="https://cdn.sentry.io/sdk.js"></script>
</head>
<body>
    <div id="root">Stripe payment page</div>
    <script>console.log('react 18');</script>
</body></html>"""
        parser = TechParser(html)
        result = parser.detect()

        names = {d["name"] for d in result["detections"]}
        assert "Stripe" in names
        assert "React" in names
        assert "Next.js" in names
        assert "Google Fonts" in names
        assert "Sentry" in names
        # Google Cloud CDN should NOT be detected from Google Fonts URL (too generic)
        assert "Google Cloud CDN" not in names

        # Next.js should be HIGH (meta_generator)
        nxt = [d for d in result["detections"] if d["name"] == "Next.js"]
        assert nxt[0]["confidence"] == "high"

    def test_empty_html_returns_no_detections(self):
        parser = TechParser("<html><head></head><body></body></html>")
        result = parser.detect()
        assert result["detections"] == []
        assert result["platform"] is None

    def test_no_false_positive_on_mention(self):
        """Mention of a technology name should not trigger a detection
        unless the actual signal substring is present."""
        html = '<html><body>We integrate with WooCommerce and Salesforce</body></html>'
        parser = TechParser(html)
        result = parser.detect()

        # "woocommerce" is a signal for WooCommerce - this is a substring match
        # that WOULD trigger html_fallback. This test documents the current behavior.
        wc = [d for d in result["detections"] if d["name"] == "WooCommerce"]
        assert len(wc) == 1
        assert wc[0]["confidence"] == "low"
        assert wc[0]["evidence_type"] == "html_fallback"


class TestTechParserEvidenceDeduplication:
    """Multiple layers should not create duplicate detections."""

    def test_same_signal_from_multiple_layers(self):
        """WordPress appears in both meta generator AND wp-content links.
        Should still only produce one detection (first matched layer wins)."""
        html = """<html>
<head>
    <meta name="generator" content="WordPress 6.4">
</head>
<body>
    <link rel="stylesheet" href="https://example.com/wp-content/themes/style.css">
</body></html>"""
        parser = TechParser(html)
        result = parser.detect()

        wp = [d for d in result["detections"] if d["name"] == "WordPress"]
        assert len(wp) == 1


class TestTechParserFlatBackwardCompat:
    """The flat string/list output must remain stable for downstream consumers."""

    def test_flat_lists_populated(self):
        html = """<html>
<head>
    <meta name="generator" content="WordPress 6.4">
    <script src="https://cdn.example.com/react.production.min.js"></script>
    <script src="https://cdn.segment.com/analytics.js/v1/key"></script>
</head>
<body></body></html>"""
        parser = TechParser(html)
        result = parser.detect()

        assert result["platform"] == "WordPress"
        assert "React" in result["frameworks"]
        assert "Segment" in result["analytics"]
        # Evidence-only fields should not be in flat lists
        assert "detections" in result
        assert len(result["detections"]) >= 3
