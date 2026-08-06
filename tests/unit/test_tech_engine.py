"""Offline unit tests for the technology detection engine (v2.2)."""

import json

import pytest

from src.scraping.parser import tech_engine as te


@pytest.fixture(autouse=True)
def _reset_catalog():
    """Each test builds its own catalog; never leak one between tests."""
    te._catalog = None
    yield
    te._catalog = None


def _load_catalog(tmp_path, technologies: dict, categories: dict | None = None):
    (tmp_path / "tech.json").write_text(json.dumps(technologies))
    if categories:
        (tmp_path / "categories.json").write_text(json.dumps(categories))
    return te._load_wappalyzer_catalog(tmp_path, meta_out={})


class TestPatternParsing:
    def test_plain_pattern(self):
        p = te._parse_pattern("wordpress")
        assert p.regex.search("Powered by WordPress")
        assert p.version_group is None
        assert p.literal == "wordpress"

    def test_version_marker_is_stripped_from_regex(self):
        p = te._parse_pattern(r"nginx(?:/([\d.]+))?\;version:\1")
        assert p.version_group == r"\1"
        # the marker must not leak into the compiled pattern
        assert "version:" not in p.regex.pattern
        assert p.regex.search("nginx/1.24.0")

    def test_confidence_marker(self):
        p = te._parse_pattern(r"maybe\;confidence:50")
        assert p.confidence == 50

    def test_malformed_regex_falls_back_to_literal(self):
        # An unbalanced group must not blow up catalog loading.
        p = te._parse_pattern("bad[(regex")
        assert p.regex.search("bad[(regex")

    def test_literal_prefilter_absent_for_regex_start(self):
        p = te._parse_pattern(r"(?:foo|bar)")
        assert p.literal is None


class TestPageSignalExtraction:
    def test_extracts_script_srcs(self):
        html = '<script src="https://cdn.x.com/a.js"></script><script src="/b.js"></script>'
        s = te.extract_page_signals(html)
        assert "https://cdn.x.com/a.js" in s.script_srcs
        assert "/b.js" in s.script_srcs

    def test_extracts_meta_tags(self):
        html = '<meta name="generator" content="WordPress 6.4"><meta property="og:site_name" content="X">'
        s = te.extract_page_signals(html)
        assert s.metas["generator"] == "WordPress 6.4"
        assert s.metas["og:site_name"] == "X"

    def test_parses_joined_set_cookie(self):
        s = te.extract_page_signals("", headers={"Set-Cookie": "a=1; Path=/, PHPSESSID=xyz; Path=/"})
        assert "phpsessid" in s.cookies
        assert s.cookies["phpsessid"] == "xyz"

    def test_html_lower_cached(self):
        s = te.extract_page_signals("<HTML>ABC</HTML>")
        assert s._html_lower == "<html>abc</html>"


class TestWappalyzerCatalog:
    def test_scriptsrc_version_extraction(self, tmp_path):
        sigs = _load_catalog(tmp_path, {
            "jQuery": {"cats": [59], "scriptSrc": [r"jquery[.-]([\d.]+)[^/]*\.js\;version:\1"]},
        })
        te._catalog = te._Catalog(sigs)
        html = '<script src="https://x.com/jquery-3.6.0.min.js"></script>'
        dets = te.detect(te.extract_page_signals(html))
        assert len(dets) == 1
        assert dets[0].name == "jQuery"
        assert dets[0].version == "3.6.0"      # trailing dot trimmed
        assert dets[0].confidence == "high"    # structural match
        assert dets[0].source == te.TARGET_SCRIPT

    def test_header_detection_with_version(self, tmp_path):
        sigs = _load_catalog(tmp_path, {
            "Nginx": {"cats": [22], "headers": {"Server": r"nginx(?:/([\d.]+))?\;version:\1"}},
        })
        te._catalog = te._Catalog(sigs)
        dets = te.detect(te.extract_page_signals("", headers={"Server": "nginx/1.24.0"}))
        assert dets[0].name == "Nginx"
        assert dets[0].version == "1.24.0"

    def test_presence_only_header(self, tmp_path):
        """An empty pattern means 'this header existing is the signal'."""
        sigs = _load_catalog(tmp_path, {"Cloudflare": {"cats": [31], "headers": {"CF-RAY": ""}}})
        te._catalog = te._Catalog(sigs)
        assert te.detect(te.extract_page_signals("", headers={"CF-RAY": "abc"}))
        # and absent header -> no detection
        assert not te.detect(te.extract_page_signals("", headers={"Server": "nginx"}))

    def test_implies_resolution(self, tmp_path):
        meta: dict = {}
        (tmp_path / "t.json").write_text(json.dumps({
            "WordPress": {"cats": [1], "implies": ["PHP", "MySQL"],
                          "meta": {"generator": "WordPress"}},
            "PHP": {"cats": [27]},
            "MySQL": {"cats": [34]},
        }))
        sigs = te._load_wappalyzer_catalog(tmp_path, meta_out=meta)
        te._catalog = te._Catalog(sigs, meta=meta)
        dets = te.detect(te.extract_page_signals('<meta name="generator" content="WordPress">'))
        names = {d.name for d in dets}
        assert names == {"WordPress", "PHP", "MySQL"}
        # implies-only technologies still resolve their real category
        by_name = {d.name: d for d in dets}
        assert by_name["MySQL"].category == "database"
        assert by_name["MySQL"].confidence == "medium"   # inferred, not observed
        assert by_name["MySQL"].source == "implies"

    def test_categories_json_overrides_numeric_map(self, tmp_path):
        sigs = _load_catalog(
            tmp_path,
            {"Thing": {"cats": [999], "headers": {"X-Thing": ""}}},
            categories={"999": {"name": "JavaScript libraries"}},
        )
        te._catalog = te._Catalog(sigs)
        dets = te.detect(te.extract_page_signals("", headers={"X-Thing": "1"}))
        assert dets[0].category == "js_library"

    def test_unknown_category_becomes_other_not_dropped(self, tmp_path):
        sigs = _load_catalog(tmp_path, {"Weird": {"cats": [4242], "headers": {"X-Weird": ""}}})
        te._catalog = te._Catalog(sigs)
        dets = te.detect(te.extract_page_signals("", headers={"X-Weird": "1"}))
        assert len(dets) == 1
        assert dets[0].category == "other"


class TestConfidence:
    def test_body_match_is_medium(self, tmp_path):
        sigs = _load_catalog(tmp_path, {"Thing": {"cats": [1], "html": ["thingy"]}})
        te._catalog = te._Catalog(sigs)
        dets = te.detect(te.extract_page_signals("<p>we use thingy</p>"))
        assert dets[0].confidence == "medium"

    def test_structural_match_is_high(self, tmp_path):
        sigs = _load_catalog(tmp_path, {"Thing": {"cats": [1], "scriptSrc": ["thingy"]}})
        te._catalog = te._Catalog(sigs)
        dets = te.detect(te.extract_page_signals('<script src="/thingy.js"></script>'))
        assert dets[0].confidence == "high"


class TestDeduplication:
    def test_same_name_different_case_merges(self, tmp_path):
        """Built-in 'nginx' and catalog 'Nginx' must not both appear."""
        sigs = _load_catalog(tmp_path, {
            "Nginx": {"cats": [22], "headers": {"Server": r"nginx(?:/([\d.]+))?\;version:\1"}},
        })
        sigs.append(te.Signature(
            name="nginx", category="web_server", target=te.TARGET_HEADER,
            key="server", patterns=[te._parse_pattern("nginx")],
        ))
        te._catalog = te._Catalog(sigs)
        dets = te.detect(te.extract_page_signals("", headers={"Server": "nginx/1.24.0"}))
        assert len(dets) == 1
        assert dets[0].version == "1.24.0"   # the richer record wins


class TestMetadataFolding:
    def test_categories_map_to_fields(self):
        dets = [
            te.Detection(name="WordPress", category="cms", confidence="high", version="6.4"),
            te.Detection(name="jQuery", category="js_library", confidence="high", version="3.6"),
            te.Detection(name="nginx", category="web_server", confidence="high"),
            te.Detection(name="MySQL", category="database", confidence="medium"),
            te.Detection(name="Odd", category="totally_unknown", confidence="medium"),
        ]
        md = te.detections_to_metadata(dets)
        assert md["platform"] == "WordPress 6.4"
        assert md["js_libraries"] == ["jQuery 3.6"]
        assert md["web_servers"] == ["nginx"]
        assert md["databases"] == ["MySQL"]
        # unknown categories are surfaced, never silently dropped
        assert md["other_technologies"] == ["Odd"]
        assert len(md["detections"]) == 5


class TestNativeSchemaV23:
    """v2.3 native-schema keys: implies, website, confidence, scope 'dom'."""

    def _detect_with_native(self, monkeypatch, entries, html="", **signal_kw):
        monkeypatch.setattr(te, "TECH_SIGNATURES", entries)
        te._catalog = None
        signals = te.extract_page_signals(html, **signal_kw)
        return te.detect(signals)

    def test_native_implies_produces_inferred_detection(self, monkeypatch):
        entries = [{
            "name": "WordPress", "category": "cms",
            "signals": ["wp-content"], "implies": ["PHP"],
        }]
        dets = self._detect_with_native(
            monkeypatch, entries, html='<link href="/wp-content/a.css">'
        )
        by_name = {d.name: d for d in dets}
        assert "PHP" in by_name
        assert by_name["PHP"].confidence == "medium"
        assert by_name["PHP"].source == "implies"

    def test_native_version_capture(self, monkeypatch):
        entries = [{
            "name": "jQuery", "category": "js_library",
            "signals": [r"jquery[.-]([\d.]+)\.min\.js\;version:\1"],
        }]
        dets = self._detect_with_native(
            monkeypatch, entries,
            html='<script src="/assets/jquery-3.6.0.min.js"></script>',
        )
        assert dets[0].version == "3.6.0"

    def test_low_confidence_override_stays_out_of_headline_fields(self, monkeypatch):
        entries = [{
            "name": "SomeVendor", "category": "cms",
            "signals": ["somevendor"], "confidence": "low",
        }]
        dets = self._detect_with_native(
            monkeypatch, entries, html="<p>We compared SomeVendor to others</p>"
        )
        assert dets[0].confidence == "low"
        meta = te.detections_to_metadata(dets)
        assert meta["platform"] is None
        assert all("SomeVendor" not in v for v in meta["other_technologies"])
        audit = [d for d in meta["detections"] if d["name"] == "SomeVendor"]
        assert audit and audit[0]["recommended"] is False

    def test_high_beats_low_in_merge(self, monkeypatch):
        entries = [
            {"name": "Thing", "category": "cms", "signals": ["thing"], "confidence": "low"},
            {"name": "Thing", "category": "cms", "signals": ["thing"],
             "scope": "meta", "meta_name": "generator"},
        ]
        dets = self._detect_with_native(
            monkeypatch, entries,
            html='<meta name="generator" content="Thing 2.0"><p>thing</p>',
        )
        assert len(dets) == 1
        assert dets[0].confidence == "high"


class TestDomEvidence:
    def _detect_with_native(self, monkeypatch, entries, html):
        monkeypatch.setattr(te, "TECH_SIGNATURES", entries)
        te._catalog = None
        return te.detect(te.extract_page_signals(html))

    def test_dom_selector_hit_is_high_confidence(self, monkeypatch):
        entries = [{
            "name": "Webflow", "category": "cms",
            "signals": ["html[data-wf-site]"], "scope": "dom",
        }]
        dets = self._detect_with_native(
            monkeypatch, entries, '<html data-wf-site="abc"><body></body></html>'
        )
        assert dets and dets[0].confidence == "high"
        assert dets[0].source == "dom"

    def test_dom_selector_miss_produces_nothing(self, monkeypatch):
        entries = [{
            "name": "Webflow", "category": "cms",
            "signals": ["html[data-wf-site]"], "scope": "dom",
        }]
        assert self._detect_with_native(monkeypatch, entries, "<html><body></body></html>") == []

    def test_malformed_selector_never_breaks_the_run(self, monkeypatch):
        entries = [
            {"name": "Bad", "category": "cms", "signals": ["[[[]nope"], "scope": "dom"},
            {"name": "nginx", "category": "web_server", "signals": ["nginx"],
             "scope": "header", "header_name": "server"},
        ]
        monkeypatch.setattr(te, "TECH_SIGNATURES", entries)
        te._catalog = None
        dets = te.detect(te.extract_page_signals("<html></html>", headers={"Server": "nginx"}))
        assert [d.name for d in dets] == ["nginx"]

    def test_external_catalog_dom_selector(self, tmp_path):
        sigs = _load_catalog(tmp_path, {
            "Elementor": {"cats": [1], "dom": {"div[data-elementor-type]": {"exists": ""}}},
        })
        dom_sigs = [s for s in sigs if s.target == te.TARGET_DOM]
        assert dom_sigs and dom_sigs[0].selectors == ["div[data-elementor-type]"]


class TestCategoryFieldGuard:
    """Every category in the compiled catalog must be deliberately routed.

    A category that is neither in CATEGORY_TO_FIELD nor in INTENTIONALLY_OTHER
    would silently pile into `other_technologies` and never render in the UI —
    the exact failure mode this guard exists to prevent.
    """

    def test_all_native_categories_are_routed(self):
        catalog = te.get_catalog()
        routed = set(te.CATEGORY_TO_FIELD) | te.INTENTIONALLY_OTHER
        unrouted = {s.category for s in catalog.signatures} - routed
        assert not unrouted, (
            f"Unrouted categories {sorted(unrouted)}: map them in "
            "CATEGORY_TO_FIELD or add them to INTENTIONALLY_OTHER explicitly."
        )

    def test_all_wappalyzer_map_categories_are_routed(self):
        routed = set(te.CATEGORY_TO_FIELD) | te.INTENTIONALLY_OTHER
        aliased = set(te._CATEGORY_NAME_ALIASES.values())
        unrouted = (set(te.WAPPALYZER_CATEGORY_MAP.values()) | aliased) - routed
        assert not unrouted, f"Unrouted external categories: {sorted(unrouted)}"

    def test_metadata_fields_exist_for_every_mapping(self):
        from src.models.scraped_data import TechStackMetadata

        meta = te.detections_to_metadata([])
        model_fields = set(TechStackMetadata.model_fields)
        for category, field_name in te.CATEGORY_TO_FIELD.items():
            if field_name in ("hosting", "email_hosting", "ssl_certificate"):
                continue  # domain-level: deliberately folded into other_technologies
            assert field_name in meta, (category, field_name)
            assert field_name in model_fields, (category, field_name)
