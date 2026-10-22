"""Stealth retry for refused pages (src/scraping/fetcher/stealth_fallback.py)."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.config.settings import get_settings
from src.scraping.fetcher import stealth_fallback as sf

REAL_PAGE = (
    "<html><head><title>Acme pricing</title></head><body>"
    + "<p>Plans and pricing for every team size.</p>" * 60
    + "</body></html>"
)
CLOUDFLARE = (
    "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>"
    '<div id="challenge-running"></div><form id="challenge-form"></form>'
    + "<p>Checking if the site connection is secure</p>" * 40
    + "</body></html>"
)
PERIMETERX = (
    "<html><head><title>Access to this page has been denied</title></head><body>"
    '<div id="px-captcha"></div>'
    + "<p>Press &amp; Hold to confirm you are a human</p>" * 40
    + "</body></html>"
)
AKAMAI = (
    "<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD><BODY>You don't have permission</BODY></HTML>"
)


# ---------------------------------------------------------------- refusal detection
@pytest.mark.parametrize(
    "status,html",
    [
        (403, CLOUDFLARE),
        (403, PERIMETERX),
        (403, AKAMAI),
        (200, PERIMETERX),  # block page served with 200
        (200, CLOUDFLARE),
        (429, REAL_PAGE),
        (0, ""),
        (200, "<html><body>hi</body></html>"),  # too small to be the page
    ],
)
def test_refusals_are_detected(status, html):
    assert sf.looks_refused(status, html)


def test_real_page_is_not_a_refusal():
    assert not sf.looks_refused(200, REAL_PAGE)


def test_should_rescue_catches_block_pages_the_status_check_misses():
    assert sf.should_rescue(200, PERIMETERX, blocked=False, captcha=False)
    assert not sf.should_rescue(200, REAL_PAGE, blocked=False, captcha=False)
    assert sf.should_rescue(403, REAL_PAGE, blocked=True, captcha=False)  # small page + 403


BIG_REAL_PAGE = (
    "<html><head><title>HubSpot Reviews | Read Customer Service Reviews</title></head><body>"
    + "<article><p>Great CRM, easy onboarding and helpful support team.</p></article>" * 2000
    + "</body></html>"
)
BIG_CHALLENGE = CLOUDFLARE.replace("</body>", "<p>padding</p>" * 12000 + "</body>")


def test_403_with_the_full_real_page_is_kept():
    # Trustpilot answers 403 yet sends the whole reviews page
    assert len(BIG_REAL_PAGE) > sf.REAL_PAGE_BYTES
    assert not sf.looks_refused(403, BIG_REAL_PAGE)
    assert not sf.should_rescue(403, BIG_REAL_PAGE, blocked=True, captcha=False)


def test_big_challenge_page_is_still_a_refusal():
    # Glassdoor's "Just a moment..." challenge is 116 KB
    assert len(BIG_CHALLENGE) > sf.REAL_PAGE_BYTES
    assert sf.looks_refused(403, BIG_CHALLENGE)


# ---------------------------------------------------------------- engine ladder
@pytest.fixture
def ladder(monkeypatch):
    calls: list[str] = []
    outcomes: dict[str, object] = {}

    def make(name):
        async def engine(url, **kw):
            calls.append(name)
            out = outcomes[name]
            if isinstance(out, BaseException):
                raise out
            status, html = out
            return sf.Rescue(name, status, html)

        return engine

    monkeypatch.setattr(sf, "ENGINES", {"first": make("first"), "second": make("second")})
    settings = get_settings().model_copy(
        update={"playwright_stealth_fallback": True, "playwright_stealth_engines": "first,second"}
    )
    return calls, outcomes, settings


async def test_first_engine_that_gets_the_page_wins(ladder):
    calls, outcomes, settings = ladder
    outcomes.update({"first": (200, REAL_PAGE), "second": (200, REAL_PAGE)})
    r = await sf.rescue("https://acme.test", settings)
    assert r.engine == "first" and calls == ["first"]


async def test_refused_engine_falls_through_to_the_next(ladder):
    calls, outcomes, settings = ladder
    outcomes.update({"first": (403, CLOUDFLARE), "second": (200, REAL_PAGE)})
    r = await sf.rescue("https://acme.test", settings)
    assert r.engine == "second" and calls == ["first", "second"]


async def test_missing_or_crashing_engines_are_skipped(ladder):
    calls, outcomes, settings = ladder
    outcomes.update(
        {"first": ImportError("not installed"), "second": RuntimeError("launch failed")}
    )
    assert await sf.rescue("https://acme.test", settings) is None
    assert calls == ["first", "second"]


async def test_all_refused_returns_none(ladder):
    calls, outcomes, settings = ladder
    outcomes.update({"first": (403, AKAMAI), "second": (403, PERIMETERX)})
    assert await sf.rescue("https://acme.test", settings) is None


async def test_setting_off_tries_nothing(ladder):
    calls, outcomes, settings = ladder
    off = settings.model_copy(update={"playwright_stealth_fallback": False})
    assert await sf.rescue("https://acme.test", off) is None
    assert calls == []


def test_engine_order_ignores_unknown_names():
    s = get_settings().model_copy(
        update={"playwright_stealth_engines": "scrapling, nope ,invisible_firefox"}
    )
    assert sf.engine_order(s) == ["scrapling", "invisible_firefox"]


def test_defaults():
    s = get_settings()
    assert s.playwright_stealth_fallback is True
    assert sf.engine_order(s) == ["invisible_firefox", "scrapling"]


# ---------------------------------------------------------------- wired into the real fetcher
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


class _Site:
    def __init__(self, status: int, body: str):
        site = self
        self.hits = 0

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                site.hits += 1
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/leadership"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


async def _fetch(monkeypatch, url, rescue_result):
    from src.scraping.fetcher import lake_playwright_fetcher as mod

    calls = []

    async def fake_rescue(u, settings, **kw):
        calls.append(u)
        return rescue_result

    monkeypatch.setattr(mod.stealth_fallback, "rescue", fake_rescue)
    f = mod.LakePlaywrightFetcher()

    async def _none(*_a, **_k):
        return None

    monkeypatch.setattr(f, "_get_redis_client", _none)
    monkeypatch.setattr(f, "_load_session", _none)
    monkeypatch.setattr(f, "_save_session", _none)
    return await f.fetch(url), calls


@needs_chromium
async def test_refused_page_is_rescued_and_not_marked_blocked(monkeypatch):
    site = _Site(403, AKAMAI)
    try:
        result, calls = await _fetch(
            monkeypatch, site.url, sf.Rescue("invisible_firefox", 200, REAL_PAGE, {"x": "1"})
        )
    finally:
        site.close()
    assert calls == [site.url]
    assert result.status_code == 200 and not result.blocked and not result.captcha_detected
    assert "Plans and pricing" in result.html


@needs_chromium
async def test_refused_page_stays_blocked_when_every_engine_fails(monkeypatch):
    site = _Site(403, AKAMAI)
    try:
        result, calls = await _fetch(monkeypatch, site.url, None)
    finally:
        site.close()
    assert calls == [site.url] and result.blocked and result.status_code == 403


@needs_chromium
async def test_normal_page_never_triggers_the_stealth_retry(monkeypatch):
    site = _Site(200, REAL_PAGE)
    try:
        result, calls = await _fetch(monkeypatch, site.url, None)
    finally:
        site.close()
    assert calls == [] and result.status_code == 200 and not result.blocked


@needs_chromium
async def test_403_with_full_real_page_is_kept_without_a_retry(monkeypatch):
    site = _Site(403, BIG_REAL_PAGE)
    try:
        result, calls = await _fetch(monkeypatch, site.url, None)
    finally:
        site.close()
    assert calls == []  # no slow stealth retry needed
    assert not result.blocked and "Great CRM" in result.html


def test_small_page_that_loaded_fine_is_not_retried():
    small = "<html><head><title>Hi</title></head><body><p>Small but real.</p></body></html>"
    assert not sf.should_rescue(200, small, blocked=False, captcha=False)


RECAPTCHA_WIDGET = '<form><div class="g-recaptcha" data-sitekey="6Lc_x"></div></form>'


@pytest.mark.parametrize("status", [200, 403])
def test_big_real_page_with_a_captcha_widget_in_a_form_is_kept(status):
    page = BIG_REAL_PAGE.replace("</body>", RECAPTCHA_WIDGET + "</body>")
    assert not sf.looks_refused(status, page)
    assert not sf.should_rescue(status, page, blocked=status == 403, captcha=True)


def test_small_captcha_wall_is_still_refused():
    wall = (
        "<html><head><title>Verify</title></head><body>"
        + RECAPTCHA_WIDGET
        + "x" * 3000
        + "</body></html>"
    )
    assert sf.looks_refused(200, wall)
    assert sf.should_rescue(200, wall, blocked=False, captcha=True)


def test_cloudflare_bot_script_on_a_normal_page_is_not_a_challenge():
    page = REAL_PAGE.replace(
        "</body>", '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script></body>'
    )
    assert not sf.looks_refused(200, page)
