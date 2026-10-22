"""Regression harness (sprint 21-22 Oct, Track B Day 2).

Pins three behaviours that the v2 pipeline depends on, end to end through the
real code paths rather than flag-by-flag:

1. Escalation: a real blocked / captcha response, run through the fetch layer's
   own detector (BaseFetcher.is_blocked -> detect_captcha), drives
   EscalationService.fetch_with_escalation to the next tier, waits only on
   rate limits, and stops at the last tier.
2. Cache hit: when a page's content_hash is unchanged, ContentWorker skips the
   specialized extraction and records no change.
3. Change detection: a changed page (or force_refresh, or the cache flag off)
   is re-extracted, and a real change is recorded with old and new hashes.

No database, Redis, browser or network: fetchers and the page_content table are
in-memory fakes; everything else is production code.
"""

from __future__ import annotations

import pytest

from src.config import settings as settings_mod
from src.models.scraped_data import DataType
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier
from src.scraping.fetcher.base import BaseFetcher
from src.scraping.parser.markdown import content_hash, html_to_markdown
from src.services import escalation as esc_mod
from src.services.escalation import EscalationService

# ---------------------------------------------------------------------- pages

GOOD_PAGE = """<!doctype html><html><head><title>Acme pricing</title></head><body>
<h1>Pricing</h1>
<div class="pricing-table">
  <div class="pricing-card"><h3>Starter</h3><p>$29 per month</p>
    <ul><li>5 seats included</li></ul></div>
  <div class="pricing-card"><h3>Growth</h3><p>$99 per month</p>
    <ul><li>25 seats included</li></ul></div>
  <div class="pricing-card"><h3>Scale</h3><p>$299 per month</p>
    <ul><li>Unlimited seats</li></ul></div>
</div>
<p>All plans include email support, single sign-on and a 14-day free trial.</p>
</body></html>"""

CLOUDFLARE_CHALLENGE = """<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title>
<meta http-equiv="refresh" content="390"></head><body class="no-js">
<div class="main-wrapper" role="main"><div class="main-content">
<h1 class="zone-name-title h1">acme.test</h1>
<h2 class="h2" id="challenge-running">Checking if the site connection is secure</h2>
<form id="challenge-form" action="/pricing?__cf_chl_f_tk=x" method="POST"></form>
</div></div></body></html>"""

RECAPTCHA_WALL = """<html><head><title>Verify you are human</title>
<script src="https://www.google.com/recaptcha/api.js" async defer></script></head>
<body><form action="/verify" method="post">
<div class="g-recaptcha" data-sitekey="6Lc_test"></div>
<button type="submit">Continue</button></form>
<p>Please complete the check to continue.</p></body></html>"""

FORBIDDEN = (
    "<html><head><title>403 Forbidden</title></head><body><h1>Access denied</h1></body></html>"
)


# ----------------------------------------------------------- escalation chain


class _CannedFetcher:
    """Returns a fixed (status, html) and runs the real fetch-layer detector on it."""

    def __init__(self, tier: ScrapingTier, status: int, html: str, calls: list):
        self.tier, self.status, self.html, self.calls = tier, status, html, calls

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        self.calls.append(self.tier)
        blocked, captcha = BaseFetcher.is_blocked(self.status, self.html)
        return FetchResult(
            url=url,
            status_code=self.status,
            html=self.html,
            tier_used=self.tier,
            cost_usd=0.0,
            duration_ms=1,
            blocked=blocked,
            captcha_detected=captcha,
        )


@pytest.fixture
def chain(monkeypatch):
    """Wire EscalationService to canned fetchers: PLAYWRIGHT -> PLAYWRIGHT_PROXY."""
    calls: list[ScrapingTier] = []
    recorded: list[tuple[ScrapingTier, bool]] = []
    sleeps: list[float] = []
    responses: dict[ScrapingTier, tuple[int, str]] = {}

    base = settings_mod.get_settings()
    s = base.model_copy(update={"lightpanda_ws_url": "", "enable_go_fetchers": False})
    monkeypatch.setattr(esc_mod, "get_settings", lambda: s)
    monkeypatch.setattr(
        esc_mod,
        "create_fetcher",
        lambda tier: _CannedFetcher(tier, *responses[tier], calls),
    )

    async def _sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(esc_mod.asyncio, "sleep", _sleep)

    svc = EscalationService(pool=None)

    async def _initial(_domain):
        return ScrapingTier.PLAYWRIGHT

    async def _record(_domain, result, success):
        recorded.append((result.tier_used, success))

    monkeypatch.setattr(svc, "decide_initial_tier", _initial)
    monkeypatch.setattr(svc, "record_result", _record)

    async def run(first: tuple[int, str], second: tuple[int, str] | None = None) -> FetchResult:
        responses[ScrapingTier.PLAYWRIGHT] = first
        if second is not None:
            responses[ScrapingTier.PLAYWRIGHT_PROXY] = second
        return await svc.fetch_with_escalation(
            "https://acme.test/pricing", "acme.test", proxy_available=True
        )

    run.calls, run.recorded, run.sleeps, run.svc = calls, recorded, sleeps, svc
    return run


async def test_clean_page_does_not_escalate(chain):
    result = await chain((200, GOOD_PAGE))
    assert chain.calls == [ScrapingTier.PLAYWRIGHT]
    assert result.tier_used == ScrapingTier.PLAYWRIGHT and not result.blocked
    assert chain.recorded == [(ScrapingTier.PLAYWRIGHT, True)]
    assert chain.sleeps == []


@pytest.mark.parametrize(
    "challenge", [CLOUDFLARE_CHALLENGE, RECAPTCHA_WALL], ids=["cloudflare", "recaptcha"]
)
async def test_captcha_path_escalates_immediately(chain, challenge):
    result = await chain((200, challenge), (200, GOOD_PAGE))
    assert chain.calls == [ScrapingTier.PLAYWRIGHT, ScrapingTier.PLAYWRIGHT_PROXY]
    assert result.tier_used == ScrapingTier.PLAYWRIGHT_PROXY and not result.captcha_detected
    assert chain.recorded == [(ScrapingTier.PLAYWRIGHT_PROXY, True)]
    assert chain.sleeps == []  # captcha is not a rate limit: no wait


def test_captcha_reason_is_reported(chain):
    blocked, captcha = BaseFetcher.is_blocked(200, CLOUDFLARE_CHALLENGE)
    r = FetchResult(
        url="u",
        status_code=200,
        html=CLOUDFLARE_CHALLENGE,
        tier_used=ScrapingTier.PLAYWRIGHT,
        cost_usd=0,
        duration_ms=1,
        blocked=blocked,
        captcha_detected=captcha,
    )
    assert (blocked, captcha) == (False, True)
    assert chain.svc.should_escalate(r) and chain.svc.get_escalation_reason(r) == "captcha"


async def test_blocked_path_403_escalates_without_waiting(chain):
    result = await chain((403, FORBIDDEN), (200, GOOD_PAGE))
    assert chain.calls == [ScrapingTier.PLAYWRIGHT, ScrapingTier.PLAYWRIGHT_PROXY]
    assert result.status_code == 200 and result.tier_used == ScrapingTier.PLAYWRIGHT_PROXY
    assert chain.sleeps == []


@pytest.mark.parametrize("status", [429, 503])
async def test_rate_limited_path_waits_before_escalating(chain, status):
    result = await chain((status, FORBIDDEN), (200, GOOD_PAGE))
    assert result.tier_used == ScrapingTier.PLAYWRIGHT_PROXY
    assert len(chain.sleeps) == 1 and chain.sleeps[0] > 0


async def test_tiny_200_response_is_a_soft_block(chain):
    result = await chain((200, "<html></html>"), (200, GOOD_PAGE))
    assert chain.calls == [ScrapingTier.PLAYWRIGHT, ScrapingTier.PLAYWRIGHT_PROXY]
    assert result.tier_used == ScrapingTier.PLAYWRIGHT_PROXY


async def test_blocked_at_last_tier_stops_and_records_failure(chain):
    result = await chain((403, FORBIDDEN), (403, FORBIDDEN))
    assert chain.calls == [ScrapingTier.PLAYWRIGHT, ScrapingTier.PLAYWRIGHT_PROXY]
    assert result.blocked and result.tier_used == ScrapingTier.PLAYWRIGHT_PROXY
    assert chain.recorded == [(ScrapingTier.PLAYWRIGHT_PROXY, False)]


# --------------------------------------------------- cache hit / content_hash


class _PageContentStore:
    """In-memory stand-in for the page_content table (get_hash / upsert)."""

    def __init__(self):
        self.hashes: dict[tuple[str, object], str] = {}
        self.upserts = 0

    async def get_hash(self, _pool, url, user_id=None):
        return self.hashes.get((url, user_id))

    async def upsert_page_content(self, _pool, *, url, content_hash, user_id=None, **_kw):
        self.upserts += 1
        self.hashes[(url, user_id)] = content_hash


@pytest.fixture
def worker_env(monkeypatch):
    from src.db.queries import page_content as pc_mod
    from src.workers.content_worker import ContentWorker

    store = _PageContentStore()
    monkeypatch.setattr(pc_mod, "get_hash", store.get_hash)
    monkeypatch.setattr(pc_mod, "upsert_page_content", store.upsert_page_content)

    flags = {"enable_scrape_cache": True}
    base = settings_mod.get_settings()
    monkeypatch.setattr(
        settings_mod,
        "get_settings",
        lambda: base.model_copy(
            update={"enable_content_persistence": True, "enable_change_monitoring": True, **flags}
        ),
    )

    def make(html_holder: dict, force_refresh: bool = False):
        w = ContentWorker(
            domain="acme.test",
            job_id="00000000-0000-0000-0000-000000000001",
            pool=object(),
            force_refresh=force_refresh,
        )
        w.fetches = 0
        w.pricing_calls = 0
        w.changes = []
        w.exported = []

        async def _fetch(url, options=None):
            w.fetches += 1
            return FetchResult(
                url=url,
                status_code=200,
                html=html_holder["html"],
                tier_used=ScrapingTier.PLAYWRIGHT,
                cost_usd=0,
                duration_ms=1,
            )

        def _pricing(url, html, rich_meta):
            w.pricing_calls += 1
            return [{"data_type": "pricing", "url": url}]

        async def _record_change(url, old_hash, new_hash, user_uuid):
            w.changes.append((old_hash, new_hash))

        async def _export(records):
            w.exported.append([r.get("data_type") for r in records])
            return len(records)

        w.fetch_page = _fetch
        w._extract_pricing = _pricing
        w._record_change = _record_change
        w.export_results = _export
        return w

    make.store, make.flags = store, flags
    return make


async def _process(worker, url="https://acme.test/pricing"):
    return await worker._process_url(url, DataType.PRICING, ["pricing"])


async def test_first_fetch_extracts_and_stores_hash(worker_env):
    page = {"html": GOOD_PAGE}
    w = worker_env(page)
    await _process(w)
    assert w.pricing_calls == 1
    assert worker_env.store.hashes  # hash persisted
    assert w.changes == []  # nothing to compare against yet


async def test_cache_hit_skips_extraction(worker_env):
    page = {"html": GOOD_PAGE}
    await _process(worker_env(page))  # warm the cache
    w = worker_env(page)
    await _process(w)
    assert w.fetches == 1  # the page is still fetched...
    assert w.pricing_calls == 0  # ...but specialized extraction is skipped
    assert w.changes == []
    assert len(w.exported) == 1 and "pricing" not in w.exported[0]  # page record only


async def test_whitespace_reflow_is_still_a_cache_hit(worker_env):
    page = {"html": GOOD_PAGE}
    await _process(worker_env(page))
    page["html"] = GOOD_PAGE.replace("\n", "\n\n   ").replace("> <", ">  <")
    assert content_hash(html_to_markdown(page["html"], find_main=True)) == content_hash(
        html_to_markdown(GOOD_PAGE, find_main=True)
    )
    w = worker_env(page)
    await _process(w)
    assert w.pricing_calls == 0 and w.changes == []


async def test_changed_content_is_re_extracted_and_change_recorded(worker_env):
    page = {"html": GOOD_PAGE}
    await _process(worker_env(page))
    old = next(iter(worker_env.store.hashes.values()))
    page["html"] = GOOD_PAGE.replace("$99", "$109")
    w = worker_env(page)
    await _process(w)
    assert w.pricing_calls == 1
    assert len(w.changes) == 1
    old_hash, new_hash = w.changes[0]
    assert old_hash == old and new_hash != old
    assert next(iter(worker_env.store.hashes.values())) == new_hash


async def test_force_refresh_bypasses_the_cache(worker_env):
    page = {"html": GOOD_PAGE}
    await _process(worker_env(page))
    w = worker_env(page, force_refresh=True)
    await _process(w)
    assert w.pricing_calls == 1 and w.changes == []


async def test_cache_flag_off_always_extracts(worker_env):
    page = {"html": GOOD_PAGE}
    await _process(worker_env(page))
    worker_env.flags["enable_scrape_cache"] = False
    w = worker_env(page)
    await _process(w)
    assert w.pricing_calls == 1


async def test_blocked_fetch_extracts_nothing_and_touches_no_cache(worker_env, monkeypatch):
    page = {"html": FORBIDDEN}
    w = worker_env(page)

    async def _blocked(url, options=None):
        return FetchResult(
            url=url,
            status_code=403,
            html=FORBIDDEN,
            tier_used=ScrapingTier.PLAYWRIGHT_PROXY,
            cost_usd=0,
            duration_ms=1,
            blocked=True,
        )

    w.fetch_page = _blocked
    assert await _process(w) == []
    assert w.pricing_calls == 0 and worker_env.store.upserts == 0 and w.exported == []


async def test_default_settings_extract_unchanged_pages_every_time(worker_env):
    """Product decision (22 Oct): extraction runs on every fetch by default."""
    default = settings_mod.Settings.model_fields["enable_scrape_cache"].default
    assert default is False
    worker_env.flags["enable_scrape_cache"] = default
    page = {"html": GOOD_PAGE}
    await _process(worker_env(page))
    w = worker_env(page)
    await _process(w)
    assert w.pricing_calls == 1  # unchanged page, still extracted
    assert w.changes == []  # and still no false change event
