"""Unit tests for the Tech Detect fetch/detect core.

No network: the SSRF guard, URL handling and classification are pure, and
detection runs against fixture HTML.
"""

import pytest

from src.services.tech_categories import CANONICAL_IDS
from src.services.tech_detect import (
    STATUS_BLOCKED,
    STATUS_FAILED,
    STATUS_OK,
    UnsafeURLError,
    assert_safe_url,
    clean_host,
    detect_sync,
    merge_wappalyzer_detections,
    normalize_input,
    url_variants,
)

WP_HTML = """
<html><head>
<meta name="generator" content="WordPress 6.4">
<link rel="stylesheet" href="/wp-content/themes/x/style.css">
<script src="https://www.googletagmanager.com/gtag/js?id=G-1"></script>
</head><body>wp-includes</body></html>
"""


class TestUrlHandling:
    def test_bare_domain_gets_scheme_and_is_variant_eligible(self):
        assert normalize_input("wordpress.org") == ("https://wordpress.org", False)

    def test_explicit_path_is_preserved_and_pinned(self):
        url, has_path = normalize_input("https://stripe.com/pricing")
        assert url == "https://stripe.com/pricing"
        assert has_path is True

    def test_query_only_counts_as_explicit(self):
        assert normalize_input("example.com?a=1")[1] is True

    @pytest.mark.parametrize("bad", ["", "   ", "https://"])
    def test_rejects_unusable_input(self, bad):
        with pytest.raises(ValueError):
            normalize_input(bad)

    def test_variants_cover_scheme_and_www(self):
        assert url_variants("Example.com")[:2] == [
            "https://example.com",
            "https://www.example.com",
        ]

    def test_clean_host_strips_scheme_www_path_and_port(self):
        assert clean_host("https://www.Foo.com:8443/bar?x=1") == "foo.com"


class TestSSRFGuard:
    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1/",
            "http://localhost/",
            "http://10.0.0.5/",
            "http://192.168.1.1/",
        ],
    )
    def test_rejects_internal_addresses(self, url):
        with pytest.raises(UnsafeURLError):
            assert_safe_url(url)

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "ftp://x/"])
    def test_rejects_non_http_schemes(self, url):
        with pytest.raises(UnsafeURLError):
            assert_safe_url(url)

    def test_rejects_unresolvable_host(self):
        with pytest.raises(UnsafeURLError):
            assert_safe_url("http://this-host-does-not-exist-abc123xyz.invalid/")


class TestDetectSync:
    def test_detects_wordpress_from_fixture(self):
        out = detect_sync("https://x.test", WP_HTML, {}, wappalyzer=False)
        names = {d["name"] for d in out["detections"]}
        assert "WordPress" in names
        assert out["platform"] == "WordPress"

    def test_header_signals_add_detections(self):
        without = detect_sync("https://x.test", WP_HTML, {}, wappalyzer=False)
        with_hdr = detect_sync(
            "https://x.test", WP_HTML, {"server": "nginx/1.24"}, wappalyzer=False
        )
        assert len(with_hdr["detections"]) > len(without["detections"])
        assert any(
            d["evidence_type"] == "header" for d in with_hdr["detections"]
        ), "header-based signatures must fire when headers are supplied"

    def test_wappalyzer_failure_does_not_break_detection(self, monkeypatch):
        import src.scraping.parser.wappalyzer_runner as wr

        monkeypatch.setattr(
            wr, "detect_wappalyzer_full", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        out = detect_sync("https://x.test", WP_HTML, {}, wappalyzer=True)
        assert any(d["name"] == "WordPress" for d in out["detections"])


class TestMergeWappalyzer:
    def test_dedupes_against_curated_and_tags_evidence(self):
        detected = detect_sync("https://x.test", WP_HTML, {}, wappalyzer=False)
        before = len(detected["detections"])
        merge_wappalyzer_detections(
            detected, {"WordPress": ["CMS"], "MySQL": ["Databases"], "PHP": ["Programming languages"]}
        )
        added = [d for d in detected["detections"] if d["evidence_type"] == "wappalyzer"]
        assert {d["name"] for d in added} == {"MySQL", "PHP"}
        assert len(detected["detections"]) == before + 2

    def test_merged_categories_normalize_to_canonical_ids(self):
        from src.services.tech_categories import normalize_category

        detected = detect_sync("https://x.test", WP_HTML, {}, wappalyzer=False)
        merge_wappalyzer_detections(
            detected, {"Elementor": ["Page builders"], "Redis": ["Databases"]}
        )
        for det in detected["detections"]:
            assert normalize_category(det["category"]) in CANONICAL_IDS


def test_status_constants_are_distinct():
    assert len({STATUS_OK, STATUS_BLOCKED, STATUS_FAILED}) == 3
