"""End-to-end detection tests against the built-in signature catalog.

tests/unit/test_tech_engine.py covers the engine's mechanics (pattern parsing,
catalog loading, dedup, folding) using small synthetic catalogs. This module
covers the shipped catalog in src/data/tech_signatures.py: given a realistic
page, are the right technologies found, at the right confidence, and — just as
importantly — are the wrong ones left alone?

These cases were carried over from the two tech_parser suites that the catalog
engine replaced, so the behaviour they locked in is still enforced.
"""

from src.scraping.parser.tech_engine import (
    detect,
    detections_to_metadata,
    extract_page_signals,
)


def run(html: str, headers: dict[str, str] | None = None, url: str = ""):
    """Detect against the built-in catalog."""
    return detect(extract_page_signals(html, url=url, headers=headers or {}))


def names(html: str, headers: dict[str, str] | None = None) -> set[str]:
    return {d.name for d in run(html, headers)}


def one(html: str, vendor: str, headers: dict[str, str] | None = None):
    """The single detection for `vendor`, asserting it was found exactly once."""
    hits = [d for d in run(html, headers) if d.name == vendor]
    assert len(hits) == 1, f"expected exactly one {vendor}, got {hits}"
    return hits[0]


class TestStructuralSignalsAreHighConfidence:
    """Header, cookie and meta matches are observed facts, not inferences."""

    def test_meta_generator_is_high_confidence(self):
        det = one(
            '<html><head><meta name="generator" content="WordPress 6.4.2"/>'
            "</head></html>",
            "WordPress",
        )
        assert det.confidence == "high"
        assert det.source == "meta"

    def test_server_header_is_high_confidence(self):
        det = one("<html></html>", "nginx", {"Server": "nginx/1.24.0"})
        assert det.confidence == "high"
        assert det.source == "header"

    def test_powered_by_header_detects_language(self):
        det = one("<html></html>", "PHP", {"X-Powered-By": "PHP/8.2.1"})
        assert det.confidence == "high"

    def test_session_cookie_detects_php(self):
        det = one("<html></html>", "PHP", {"Set-Cookie": "PHPSESSID=abc; Path=/"})
        assert det.confidence == "high"

    def test_csrf_cookie_detects_django(self):
        assert "Django" in names("<html></html>", {"Set-Cookie": "csrftoken=xyz; Path=/"})

    def test_multiple_joined_cookies_are_split(self):
        blob = "PHPSESSID=abc; Path=/, visid_incap_123=xyz; Path=/"
        found = names("<html></html>", {"Set-Cookie": blob})
        assert "PHP" in found
        assert "Imperva (Incapsula)" in found

    def test_body_match_is_medium_confidence(self):
        det = one(
            '<html><body><script src="https://cdn.shopify.com/s/f.js"></script>'
            "</body></html>",
            "Shopify",
        )
        assert det.confidence == "medium"


class TestBuiltInCatalogFindsRealStacks:
    def test_cms_from_asset_domain(self):
        assert "Shopify" in names(
            '<html><script src="https://cdn.shopify.com/s/files/1/app.js"></script></html>'
        )

    def test_js_library_and_version(self):
        det = one(
            '<html><script src="/assets/jquery-3.6.0.min.js"></script></html>',
            "jQuery",
        )
        assert det.name == "jQuery"

    def test_framework_from_inline_global(self):
        assert "Next.js" in names(
            '<html><body><script id="__NEXT_DATA__" type="application/json">{}'
            "</script></body></html>"
        )

    def test_font_provider_from_link(self):
        assert "Google Fonts" in names(
            '<html><head><link href="https://fonts.googleapis.com/css?family=Inter"'
            ' rel="stylesheet"></head></html>'
        )

    def test_chat_widget(self):
        assert "Intercom" in names(
            '<html><script src="https://widget.intercom.io/widget/abc123"></script></html>'
        )

    def test_cookie_consent_widget(self):
        assert names(
            '<html><script src="https://cdn.cookielaw.org/otSDKStub.js"></script></html>'
        ) & {"OneTrust", "Cookie consent tools"}

    def test_monitoring_tool(self):
        assert "Sentry" in names(
            '<html><script src="https://browser.sentry-cdn.com/7.0.0/bundle.min.js">'
            "</script></html>"
        )

    def test_server_os_from_header_banner(self):
        found = names("<html></html>", {"Server": "Apache/2.4.41 (Ubuntu)"})
        assert "Apache" in found
        assert "Ubuntu" in found

    def test_realistic_mixed_page(self):
        html = """
        <html><head>
          <meta name="generator" content="WordPress 6.4"/>
          <link href="https://fonts.googleapis.com/css?family=Inter" rel="stylesheet">
          <script src="https://www.googletagmanager.com/gtm.js?id=GTM-XYZ"></script>
          <script src="https://js.stripe.com/v3/"></script>
        </head><body>
          <script src="https://widget.intercom.io/widget/abc"></script>
        </body></html>
        """
        found = names(html, {"Server": "nginx", "X-Powered-By": "PHP/8.1"})
        for expected in (
            "WordPress", "Google Fonts", "Google Tag Manager",
            "Stripe", "Intercom", "nginx", "PHP",
        ):
            assert expected in found, f"missing {expected} from {sorted(found)}"

    def test_flat_lists_and_detections_are_populated(self):
        meta = detections_to_metadata(
            run(
                '<html><head><meta name="generator" content="WordPress 6.4"/>'
                '<script src="https://www.google-analytics.com/analytics.js">'
                "</script></head></html>"
            )
        )
        assert meta["platform"] == "WordPress"
        assert "Google Analytics" in meta["analytics"]
        assert meta["detections"], "expected a per-detection audit trail"
        first = meta["detections"][0]
        # The audit trail must carry everything DetectedTech requires.
        for key in ("name", "category", "confidence", "evidence",
                    "evidence_type", "recommended"):
            assert key in first, f"detection missing {key}"


class TestNoDetectionsWithoutEvidence:
    def test_empty_html_detects_nothing(self):
        assert run("") == []

    def test_plain_page_detects_nothing(self):
        assert run("<html><body><h1>Hello world</h1></body></html>") == []


class TestFalsePositiveDiscipline:
    """Regression tests for the measured accuracy work.

    Body text may not be used to claim infrastructure. These are the exact
    cases that produced bogus detections before the cdn/hosting signals were
    tightened, so each one is a guard against reintroducing them.
    """

    def test_cdn_asset_url_does_not_imply_cdn_hosting(self):
        # Loading a library from CloudFront says nothing about who serves us.
        assert "AWS CloudFront" not in names(
            '<html><script src="https://d1abcd.cloudfront.net/lib.js"></script></html>'
        )

    def test_cdnjs_does_not_imply_cloudflare(self):
        assert "Cloudflare" not in names(
            '<html><script src="https://cdnjs.cloudflare.com/ajax/libs/x/x.js">'
            "</script></html>"
        )

    def test_naming_a_vendor_in_copy_is_not_evidence(self):
        found = names(
            "<html><body><p>Our infrastructure runs on Cloudflare, Fastly "
            "and Imperva.</p></body></html>"
        )
        assert not found & {"Cloudflare", "Fastly", "Imperva (Incapsula)"}

    def test_linking_a_vendor_marketing_site_is_not_hosting_evidence(self):
        assert "Google Cloud" not in names(
            '<html><body><a href="https://cloud.google.com/run">Built with GCP</a>'
            "</body></html>"
        )

    def test_customer_logo_mention_does_not_imply_platform(self):
        # The original live failure: a page name-dropping Shopify.
        assert "Shopify" not in names(
            "<html><body><h2>Our customers</h2><p>Shopify</p></body></html>"
        )

    def test_wp_content_themes_does_not_imply_ghost(self):
        assert "Ghost" not in names(
            '<html><link href="/wp-content/themes/x/style.css" rel="stylesheet"></html>'
        )

    def test_vendor_unique_shared_cloud_subdomain_still_detects_that_vendor(self):
        # The flip side: a vendor's own bucket on a shared cloud identifies the
        # vendor precisely. It must not be collateral damage of the rule above.
        found = names(
            '<html><script src="https://d2wy8f7a9ursnm.cloudfront.net/v7/bugsnag.min.js">'
            "</script></html>"
        )
        assert "Bugsnag" in found
        assert "AWS CloudFront" not in found


class TestScalarFieldsPreferStrongestEvidence:
    """`platform` and `server_os` hold one value, so confidence must decide it.

    Detections arrive sorted by (category, name), so picking the first match
    would pick alphabetically. Found live against github.com, which loads
    images from Contentful's CDN.
    """

    def test_high_confidence_cms_beats_alphabetically_earlier_medium(self):
        html = (
            '<html><head><meta name="generator" content="WordPress 6.4"/></head>'
            '<body><img src="https://images.ctfassets.net/x/y.png"></body></html>'
        )
        meta = detections_to_metadata(run(html))
        found = {d.name for d in run(html)}
        # Both are detected — Contentful is genuinely in use as an asset host …
        assert {"WordPress", "Contentful"} <= found
        # … but the self-declared platform is the one reported.
        assert meta["platform"].startswith("WordPress")

    def test_medium_confidence_cms_is_still_reported_when_alone(self):
        meta = detections_to_metadata(
            run('<html><body><img src="https://images.ctfassets.net/x/y.png"></body></html>')
        )
        assert meta["platform"] == "Contentful"


class TestStructuralBeatsBodyOnConflict:
    def test_same_vendor_from_two_layers_yields_one_detection_at_high(self):
        html = (
            '<html><head><meta name="generator" content="WordPress 6.4"/></head>'
            '<body><link href="/wp-content/themes/t/s.css"></body></html>'
        )
        det = one(html, "WordPress")
        assert det.confidence == "high"


class TestDepthProgramRegressions:
    """Corpus-driven fixes (v2.3). Each test names the live site that produced
    the original false positive or coverage gap."""

    def test_webflow_not_detected_from_wf_page_prefix_collision(self):
        # hubspot.com: an unrelated web-font loader defines .wf-page-header —
        # the old bare "wf-page" signal claimed Webflow from it.
        html = """<html><head><style>
        .wf-page-header{--wf-page-header-height: calc(100dvh - 10px);width:100%}
        </style></head><body></body></html>"""
        assert "Webflow" not in names(html)

    def test_webflow_detected_from_real_install_markers(self):
        html = '<html data-wf-page="abc123" data-wf-site="def456"><body></body></html>'
        det = one(html, "Webflow")
        assert det.confidence == "high"  # dom selector = structural evidence

    def test_svelte_not_detected_from_docs_link(self):
        # vercel.com: a footer nav link to Vercel's own SvelteKit docs page.
        html = ('<html><body><a href="/docs/frameworks/full-stack/sveltekit">'
                "SvelteKit</a> and svelte guides</body></html>")
        found = names(html)
        assert "Svelte" not in found
        assert "SvelteKit" not in found

    def test_svelte_detected_from_scoped_class_hashes(self):
        html = '<html><body><div class="card svelte-1x8fj2p">hi</div></body></html>'
        assert "Svelte" in names(html)

    def test_sveltekit_build_path_implies_svelte(self):
        html = '<script src="/_app/immutable/entry/start.abc123.js"></script>'
        found = names(html)
        assert "SvelteKit" in found
        assert "Svelte" in found  # via implies

    def test_hugo_detected_from_generator(self):
        # ghost.org: the marketing site is Hugo-built and announces it; the
        # catalog previously had no Hugo entry at all.
        det = one('<meta name="generator" content="Hugo 0.119.0">', "Hugo")
        assert det.confidence == "high"
