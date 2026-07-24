"""Offline unit tests for the v2.1 tech-stack fingerprint engine."""

from src.scraping.parser.tech_parser import TechParser


class TestHtmlScopedSignals:
    def test_cms_detection(self):
        html = '<script src="https://cdn.shopify.com/s/foo.js"></script>'
        assert TechParser(html).detect()["platform"] == "Shopify"

    def test_js_library_detection(self):
        html = '<script src="jquery.min.js"></script><script src="lodash.min.js"></script>'
        result = TechParser(html).detect()
        assert "jQuery" in result["js_libraries"]
        assert "Lodash" in result["js_libraries"]

    def test_framework_detection(self):
        html = "react.production.min.js reactdom __NEXT_DATA__"
        result = TechParser(html).detect()
        assert "React" in result["frameworks"]
        assert "Next.js" in result["frameworks"]

    def test_typescript_via_sourcemap(self):
        html = '<script src="app.js"></script><!-- app.js.map references app.ts.map -->'
        result = TechParser(html).detect()
        assert "TypeScript" in result["programming_languages"]

    def test_no_false_positive_on_empty_page(self):
        result = TechParser("<html><body>hello world</body></html>").detect()
        assert result["platform"] is None
        assert result["frameworks"] == []
        assert result["cdn"] == []


class TestHeaderScopedSignals:
    def test_web_server_from_server_header(self):
        result = TechParser("<html></html>", {"Server": "nginx/1.24.0"}).detect()
        assert result["web_servers"] == ["nginx"]

    def test_server_header_word_boundary_not_confused(self):
        # "Apache" must not match when Server explicitly says nginx
        result = TechParser("<html></html>", {"Server": "nginx/1.24.0"}).detect()
        assert "Apache" not in result["web_servers"]

    def test_os_from_server_header_banner(self):
        result = TechParser("<html></html>", {"Server": "Apache/2.4.41 (Ubuntu)"}).detect()
        assert result["server_os"] == "Ubuntu"
        assert result["web_servers"] == ["Apache"]

    def test_cdn_from_header_name_presence(self):
        # CF-RAY is a header NAME whose mere presence signals Cloudflare —
        # the header's value itself is just a request ray id, not "cloudflare".
        result = TechParser("<html></html>", {"CF-RAY": "8a1b2c3d4e5f"}).detect()
        assert "Cloudflare" in result["cdn"]

    def test_cdn_from_vercel_header(self):
        result = TechParser("<html></html>", {"X-Vercel-Id": "abc123"}).detect()
        assert "Vercel" in result["cdn"]

    def test_language_from_powered_by_header(self):
        result = TechParser("<html></html>", {"X-Powered-By": "PHP/8.2.1"}).detect()
        assert "PHP" in result["programming_languages"]

    def test_express_framework_from_powered_by(self):
        result = TechParser("<html></html>", {"X-Powered-By": "Express"}).detect()
        assert "Express" in result["frameworks"]


class TestCookieScopedSignals:
    def test_php_from_session_cookie(self):
        result = TechParser("<html></html>", {"Set-Cookie": "PHPSESSID=abc123; Path=/"}).detect()
        assert "PHP" in result["programming_languages"]

    def test_django_from_csrftoken_cookie(self):
        result = TechParser("<html></html>", {"Set-Cookie": "csrftoken=xyz; Path=/"}).detect()
        assert "Django" in result["frameworks"]

    def test_aspnet_from_session_cookie(self):
        result = TechParser("<html></html>", {"Set-Cookie": "ASP.NET_SessionId=abc; Path=/"}).detect()
        assert "ASP.NET" in result["frameworks"]
        assert "ASP.NET / C#" in result["programming_languages"]

    def test_multiple_joined_cookies(self):
        # Playwright's all_headers() may join multiple Set-Cookie values
        cookie_blob = "_shopify_s=abc; Path=/, PHPSESSID=xyz; Path=/"
        result = TechParser("<html></html>", {"Set-Cookie": cookie_blob}).detect()
        assert "PHP" in result["programming_languages"]


class TestWidgetDetection:
    def test_chat_widget(self):
        html = "widget.intercom.io crisp.chat"
        result = TechParser(html).detect()
        assert "Intercom" in result["widgets"]
        assert "Crisp" in result["widgets"]

    def test_cookie_consent_widget(self):
        result = TechParser("cookielaw.org onetrust.com").detect()
        assert "OneTrust" in result["widgets"]


class TestCombinedSignals:
    def test_full_page_realistic_mix(self):
        html = """
        <script src="https://cdn.shopify.com/s/foo.js"></script>
        <script src="https://www.googletagmanager.com/gtm.js"></script>
        <script src="jquery.min.js"></script>
        widget.intercom.io
        """
        headers = {
            "Server": "nginx/1.24.0 (Ubuntu)",
            "X-Powered-By": "Express",
            "CF-RAY": "8a1b2c3d4e5f",
        }
        result = TechParser(html, headers).detect()
        assert result["platform"] == "Shopify"
        assert "Google Tag Manager" in result["analytics"]
        assert "jQuery" in result["js_libraries"]
        assert "Intercom" in result["widgets"]
        assert "nginx" in result["web_servers"]
        assert result["server_os"] == "Ubuntu"
        assert "Cloudflare" in result["cdn"]
        assert "Node.js" in result["programming_languages"]
