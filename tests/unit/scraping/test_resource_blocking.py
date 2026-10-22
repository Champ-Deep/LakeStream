"""Resource blocking in the Playwright fetchers (sprint 21-22 Oct, Track B Day 1).

The key test runs the real LakePlaywrightFetcher end to end against two local
HTTP servers (a first-party site and a stand-in "tracker" host) and proves that
pricing_parser and contact_parser get identical results with blocking on and
off, while images, fonts, media and the tracker are never downloaded.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.config.settings import get_settings
from src.models.scraping import FetchOptions
from src.scraping.fetcher import resource_blocking as rb
from src.scraping.parser.contact_parser import ContactParser
from src.scraping.parser.pricing_parser import PricingParser


# ---------------------------------------------------------------- decision rules
@pytest.mark.parametrize("rtype", ["image", "media", "font", "ping"])
def test_heavy_resource_types_are_blocked(rtype):
    assert rb.block_reason("https://acme.test/a", rtype) == rtype


@pytest.mark.parametrize(
    "rtype", ["document", "script", "xhr", "fetch", "stylesheet", "websocket", "other"]
)
def test_dom_building_types_are_allowed(rtype):
    assert rb.block_reason("https://acme.test/a", rtype) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.google-analytics.com/analytics.js",
        "https://region1.google-analytics.com/g/collect",
        "https://www.googletagmanager.com/gtm.js?id=GTM-X",
        "https://securepubads.g.doubleclick.net/tag/js/gpt.js",
        "https://connect.facebook.net/en_US/fbevents.js",
        "https://static.hotjar.com/c/hotjar-1.js",
        "https://cdn.segment.com/analytics.js/v1/x/analytics.min.js",
        "https://snap.licdn.com/li.lms-analytics/insight.min.js",
        "https://js.hs-analytics.net/analytics/1/2.js",
    ],
)
def test_tracker_hosts_are_blocked_whatever_the_type(url):
    assert rb.block_reason(url, "script", page_host="acme.test") == "tracker"


@pytest.mark.parametrize(
    "url",
    [
        "https://notgoogle-analytics.com/x.js",  # lookalike, not a subdomain
        "https://google-analytics.com.evil.test/x",  # tracker name as a label of another domain
        "https://js.hsforms.net/forms/v2.js",  # HubSpot forms carry page content
        "https://widget.intercom.io/widget/abc",  # chat widgets are not on the list
        "https://cdn.jsdelivr.net/npm/x.js",
    ],
)
def test_non_trackers_are_allowed(url):
    assert rb.block_reason(url, "script", page_host="acme.test") is None


def test_main_document_is_never_blocked_even_on_a_tracker_site():
    assert (
        rb.block_reason("https://www.hotjar.com/pricing", "document", is_main_document=True) is None
    )


def test_tracker_vendor_own_site_is_first_party():
    # scraping amplitude.com: its own scripts build the page and must load
    assert (
        rb.block_reason("https://cdn.amplitude.com/app.js", "script", page_host="amplitude.com")
        is None
    )
    assert (
        rb.block_reason("https://cdn.amplitude.com/app.js", "script", page_host="www.amplitude.com")
        is None
    )
    # ...but the same vendor on someone else's site is still tracking
    assert (
        rb.block_reason("https://cdn.amplitude.com/app.js", "script", page_host="acme.test")
        == "tracker"
    )


def test_subdomain_tracker_entry_is_first_party_on_its_own_site():
    # ws.zoominfo.com is listed; on www.zoominfo.com it is the site's own host
    url = "https://ws.zoominfo.com/pixel/abc"
    assert rb.block_reason(url, "script", page_host="www.zoominfo.com") is None
    assert rb.block_reason(url, "script", page_host="acme.test") == "tracker"


@pytest.mark.parametrize(
    "host,site",
    [
        ("www.zoominfo.com", "zoominfo.com"),
        ("a.b.example.com", "example.com"),
        ("shop.acme.co.uk", "acme.co.uk"),
        ("acme.com.au", "acme.com.au"),
        ("127.0.0.1", "127.0.0.1"),
        ("localhost", "localhost"),
    ],
)
def test_site_of(host, site):
    assert rb.site_of(host) == site


def test_different_sites_under_same_country_sld_are_not_first_party():
    assert not rb._same_site("acme.co.uk", "tracker.co.uk")


def test_keep_visuals_lets_images_and_fonts_through_but_not_trackers():
    assert rb.block_reason("https://acme.test/logo.png", "image", keep_visuals=True) is None
    assert rb.block_reason("https://acme.test/a.woff2", "font", keep_visuals=True) is None
    assert rb.block_reason("https://acme.test/beacon", "ping", keep_visuals=True) == "ping"
    assert (
        rb.block_reason("https://www.google-analytics.com/a.js", "script", keep_visuals=True)
        == "tracker"
    )


def test_setting_defaults_on():
    assert get_settings().playwright_block_resources is True


async def test_setting_off_installs_nothing():
    class Page:
        routed = False

        async def route(self, *_a, **_k):
            Page.routed = True

    off = get_settings().model_copy(update={"playwright_block_resources": False})
    assert await rb.maybe_install_resource_blocking(Page(), off) is None
    assert Page.routed is False


# ---------------------------------------------------------------- end to end
def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return True
    except Exception:
        return False


needs_chromium = pytest.mark.skipif(
    not _chromium_available(), reason="Playwright Chromium not installed"
)

PLANS = [
    {"name": "Starter", "price": "$29", "features": ["5 seats included", "Email support"]},
    {"name": "Growth", "price": "$99", "features": ["25 seats included", "Priority support"]},
    {"name": "Scale", "price": "$299", "features": ["Unlimited seats", "Dedicated manager"]},
]

APP_JS = """
fetch('/plans.json').then(r => r.json()).then(plans => {
  const root = document.getElementById('plans');
  for (const p of plans) {
    const card = document.createElement('div');
    card.className = 'pricing-card';
    card.innerHTML = '<h3>' + p.name + '</h3><p>' + p.price + ' per month</p><ul>' +
      p.features.map(f => '<li>' + f + '</li>').join('') + '</ul><button>Start free trial</button>';
    root.appendChild(card);
  }
  document.body.setAttribute('data-plans-rendered', 'yes');
});
"""

TRACKER_JS = """
document.addEventListener('DOMContentLoaded', () => {
  const d = document.createElement('div'); d.id = 'tracker-ran'; document.body.appendChild(d);
});
"""


def _page_html(tracker_origin: str) -> str:
    person = {
        "@type": "Person",
        "name": "Priya Raman",
        "jobTitle": "Head of Sales",
        "email": "priya.raman@acme.test",
    }
    return f"""<!doctype html><html><head><title>Acme pricing</title>
<link rel="stylesheet" href="/style.css">
<script type="application/ld+json">{json.dumps(person)}</script>
<script src="{tracker_origin}/tracker.js"></script>
</head><body>
<img src="/logo.png" alt="Acme logo">
<video src="/intro.mp4" preload="auto" muted></video>
<h1>Pricing</h1>
<div id="plans" class="pricing-table"></div>
<section class="about-team">
  <div class="team-member"><h3>Rahul Mehta</h3><p class="title">CEO</p>
    <a href="https://www.linkedin.com/in/rahul-mehta-test">LinkedIn</a></div>
  <div class="team-member"><h3>Ana Silva</h3><p class="title">CTO</p></div>
</section>
<script src="/app.js"></script>
</body></html>"""


class _Site:
    """A tiny HTTP server that records every path it serves."""

    def __init__(self, routes: dict[str, tuple[str, bytes]]):
        self.routes = routes
        self.hits: list[str] = []
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                path = self.path.split("?")[0]
                site.hits.append(path)
                ctype, body = site.routes.get(path, ("text/plain", b"not found"))
                self.send_response(200 if path in site.routes else 404)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def sites():
    tracker = _Site({"/tracker.js": ("application/javascript", TRACKER_JS.encode())})
    tracker_origin = f"http://localhost:{tracker.port}"  # different host from 127.0.0.1
    first = _Site(
        {
            "/pricing": ("text/html; charset=utf-8", _page_html(tracker_origin).encode()),
            "/app.js": ("application/javascript", APP_JS.encode()),
            "/plans.json": ("application/json", json.dumps(PLANS).encode()),
            "/style.css": (
                "text/css",
                b"@font-face{font-family:Acme;src:url(/acme.woff2)} body{font-family:Acme}",
            ),
            "/logo.png": ("image/png", b"\x89PNG\r\n\x1a\n" + b"\0" * 50_000),
            "/acme.woff2": ("font/woff2", b"\0" * 40_000),
            "/intro.mp4": ("video/mp4", b"\0" * 200_000),
        }
    )
    yield first, tracker
    first.close()
    tracker.close()


async def _fetch_with(monkeypatch, url: str, *, block: bool, screenshot: bool = False):
    from src.scraping.fetcher import lake_playwright_fetcher as mod

    settings = get_settings().model_copy(
        update={"playwright_block_resources": block, "playwright_headless": True}
    )
    monkeypatch.setattr(mod, "get_settings", lambda: settings)
    monkeypatch.setattr(rb, "TRACKER_HOSTS", frozenset({"localhost"}))

    fetcher = mod.LakePlaywrightFetcher()

    async def _none(*_a, **_k):
        return None

    monkeypatch.setattr(fetcher, "_get_redis_client", _none)
    monkeypatch.setattr(fetcher, "_load_session", _none)
    monkeypatch.setattr(fetcher, "_save_session", _none)
    return await fetcher.fetch(url, FetchOptions(capture_screenshot=screenshot))


@needs_chromium
async def test_structured_extractors_get_the_full_dom_with_blocking_on(monkeypatch, sites):
    first, tracker = sites
    url = f"http://127.0.0.1:{first.port}/pricing"

    off = await _fetch_with(monkeypatch, url, block=False)
    off_hits, off_tracker_hits = list(first.hits), list(tracker.hits)
    first.hits.clear()
    tracker.hits.clear()
    on = await _fetch_with(monkeypatch, url, block=True)
    on_hits, on_tracker_hits = list(first.hits), list(tracker.hits)

    assert off.status_code == on.status_code == 200
    assert not on.blocked

    # 1. the parsers see the same thing, and it is the JS-rendered content
    off_plans = PricingParser(off.html, url).extract_pricing_plans()
    on_plans = PricingParser(on.html, url).extract_pricing_plans()
    assert [p["plan_name"] for p in on_plans] == ["Starter", "Growth", "Scale"]
    assert on_plans == off_plans
    off_people = ContactParser(off.html, url).extract_people()
    on_people = ContactParser(on.html, url).extract_people()
    assert len(on_people) >= 2 and on_people == off_people
    assert 'data-plans-rendered="yes"' in on.html
    assert '<img src="/logo.png"' in on.html  # tags stay, only the bytes are skipped

    # 2. first-party DOM-building requests still happened
    for path in ("/pricing", "/app.js", "/plans.json", "/style.css"):
        assert path in on_hits

    # 3. the heavy and tracking requests did not
    assert "/logo.png" in off_hits and "/logo.png" not in on_hits
    assert "/acme.woff2" not in on_hits
    assert "/intro.mp4" not in on_hits
    assert off_tracker_hits == ["/tracker.js"] and on_tracker_hits == []
    assert 'id="tracker-ran"' in off.html and 'id="tracker-ran"' not in on.html


@needs_chromium
async def test_screenshot_requests_keep_images_but_still_drop_trackers(monkeypatch, sites):
    first, tracker = sites
    url = f"http://127.0.0.1:{first.port}/pricing"
    result = await _fetch_with(monkeypatch, url, block=True, screenshot=True)
    assert result.status_code == 200
    assert "/logo.png" in first.hits
    assert tracker.hits == []


# ---------------------------------------------------------------- contact parser: visible text only
def test_contact_parser_ignores_emails_hidden_in_scripts():
    html = """<html><body>
    <script>window.cfg = {"dsn": "https://3d1e0e6b0f934f46bb8915f9c81ee308@www.semrush.com/1"}</script>
    <script>snaptr('init', 'x', {'user_email': 'colin.chang@zoominfo.com'})</script>
    <noscript><img src="https://px.test/?e=hidden.person@acme.test"></noscript>
    <style>/* owner: style.person@acme.test */</style>
    <p>Talk to our team: <a href="mailto:priya.raman@acme.test?subject=Hi">Priya</a></p>
    <p>Or write to ana.silva@acme.test for partnerships.</p>
    </body></html>"""
    people = ContactParser(html, "https://acme.test/contact").extract_people()
    emails = sorted(p["email"] for p in people if p.get("email"))
    assert emails == ["ana.silva@acme.test", "priya.raman@acme.test"]


def test_contact_parser_still_reads_json_ld_inside_scripts():
    html = """<html><body>
    <script type="application/ld+json">{"@type": "Person", "name": "Priya Raman",
      "jobTitle": "Head of Sales", "email": "priya.raman@acme.test"}</script>
    <script>var junk = "fake.person@tracker.test";</script>
    </body></html>"""
    people = ContactParser(html, "https://acme.test/team").extract_people()
    assert [(p["first_name"], p["last_name"], p["source"]) for p in people] == [
        ("Priya", "Raman", "json_ld")
    ]


def test_contact_parser_does_not_glue_words_across_tags():
    html = "<html><body><p>mail john.doe@acme.test</p><p>Call us today</p></body></html>"
    people = ContactParser(html, "https://acme.test/").extract_people()
    assert [p["email"] for p in people] == ["john.doe@acme.test"]


def test_contact_parser_reads_visible_linkedin_profile_links_not_hidden_ones():
    html = """<html><body>
    <script>var team = ["https://www.linkedin.com/in/hidden-only-in-script"];</script>
    <p>Our CEO <a href="https://www.linkedin.com/in/hschuck/">Henry</a></p>
    </body></html>"""
    people = ContactParser(html, "https://acme.test/about").extract_people()
    urls = [p["linkedin_url"] for p in people if p.get("linkedin_url")]
    assert urls == ["https://www.linkedin.com/in/hschuck"]
