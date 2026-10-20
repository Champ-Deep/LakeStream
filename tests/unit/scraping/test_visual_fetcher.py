"""Tests for the visual tier (LakeVisualFetcher): no browser, no network.

The probe loop is replaced by canned traces; the real ``merge_facts`` and
``classify`` run against the fixtures under tests/fixtures/visual (synthetic
pages, no client data). What matters is the contract: flag off -> failed;
reached -> success with cited facts, HTML and screenshot; a wall -> blocked
with an explicit status; missing fields stay null; the factory maps
ScrapingTier.VISUAL to this fetcher.
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import patch

import pytest

from src.config.constants import TIER_COSTS
from src.config.settings import get_settings
from src.models.scraping import FetchResult, ScrapingTier
from src.scraping.fetcher import lake_visual_fetcher as lvf
from src.scraping.fetcher.factory import create_fetcher

FIXTURES = Path(__file__).parents[2] / "fixtures" / "visual"
URL = "https://example.test/company/acme-corp/"
COMPANY_HTML = (FIXTURES / "company_page.html").read_text(encoding="utf-8")
LOGIN_WALL_HTML = (FIXTURES / "login_wall.html").read_text(encoding="utf-8")


def _trace(outcome: str, steps: list[dict], *, landing_status: int = 200, reason=None) -> dict:
    return {
        "url": URL, "started_at": "2026-10-19T10:00:00+00:00", "outcome": outcome,
        "reason": reason, "landing": {"status": landing_status, "outcome": "ok", "signals": []},
        "steps": steps, "model_calls": 2, "vision_cost_usd": 0.0003, "jev_tokens": 900,
        "total_ms": 1500,
    }


def _step(observations: dict) -> dict:
    return {
        "step": 1, "t_ms": 500,
        "before": {"url": URL, "png": "step_1.before.png"},
        "after": {"url": URL, "png": "step_1.after.png"},
        "vision": {"observations": observations},
    }


FULL_OBS = {
    "name": "Acme Corp", "headline_or_role": "Software Development",
    "location": "Springfield, Illinois", "about_or_bio": None,
    "recent_posts": [{"text_excerpt": "We launched the Widget X returns console today",
                      "date_label_as_shown": "2d"}],
    "topics_observed": ["Returns"], "other_visible_facts": ["12,345 followers"],
}


def _fake_loop(trace: dict, html: str | None = COMPANY_HTML, *, raise_exc=None):
    """A stand-in for scripts/visual_probe/loop with a canned run()."""

    async def run(url, out_dir, *, html_sink=None, **_):
        if raise_exc:
            raise raise_exc
        out_dir.mkdir(parents=True, exist_ok=True)
        for step in trace["steps"]:
            (out_dir / step["before"]["png"]).write_bytes(b"before-png")
            (out_dir / step["after"]["png"]).write_bytes(b"after-png")
        if html_sink is not None and html is not None:
            html_sink["html"] = html
        return trace

    return types.SimpleNamespace(run=run)


@pytest.fixture
def visual_enabled(tmp_path):
    settings = get_settings()
    with (
        patch.object(settings, "enable_visual_fetcher", True),
        patch.object(settings, "screenshot_dir", str(tmp_path)),
    ):
        yield settings


def _patched(loop_mod):
    _, extract = lvf._probe_modules()
    return patch.object(lvf, "_probe_modules", return_value=(loop_mod, extract))


class TestFlag:
    @pytest.mark.asyncio
    async def test_flag_off_returns_failed_without_running(self):
        settings = get_settings()
        with (
            patch.object(settings, "enable_visual_fetcher", False),
            patch.object(lvf, "_probe_modules", side_effect=AssertionError("must not run")),
        ):
            result = await lvf.LakeVisualFetcher().fetch(URL)

        assert isinstance(result, FetchResult)
        assert result.tier_used == ScrapingTier.VISUAL
        assert result.blocked is True and result.html == ""
        assert result.facts["status"] == "failed"
        assert "disabled" in result.facts["reason"]

    def test_factory_maps_visual_tier(self):
        assert isinstance(create_fetcher(ScrapingTier.VISUAL), lvf.LakeVisualFetcher)


class TestOutcomes:
    @pytest.mark.asyncio
    async def test_reached_is_success_with_cited_facts(self, visual_enabled):
        trace = _trace("reached", [_step(FULL_OBS)], reason="posts/activity visible")
        with _patched(_fake_loop(trace)):
            result = await lvf.LakeVisualFetcher().fetch(URL)

        assert result.blocked is False and result.status_code == 200
        assert result.html == COMPANY_HTML
        assert result.screenshot_bytes == b"after-png"
        assert result.cost_usd == pytest.approx(TIER_COSTS["visual"] + 0.0003)
        facts = result.facts
        assert facts["status"] == "success" and facts["producer"] == "visual_probe"
        name = facts["facts"]["name"]
        assert name["value"] == "Acme Corp"
        assert name["source_url"] == URL
        assert name["screenshot"] == "step_1.before.png"
        assert name["captured_at"].startswith("2026-10-19T10:00:00.500")
        post = facts["facts"]["recent_posts"][0]
        assert post["id"] == "post1" and post["date_label_as_shown"] == "2d"
        assert facts["facts"]["topics"][0]["kind"] == "observed"

    @pytest.mark.asyncio
    async def test_missing_fields_stay_null(self, visual_enabled):
        trace = _trace("reached", [_step({"name": "Acme Corp"})])
        with _patched(_fake_loop(trace)):
            result = await lvf.LakeVisualFetcher().fetch(URL)

        f = result.facts["facts"]
        assert f["name"]["value"] == "Acme Corp"
        assert f["headline_or_role"] is None and f["location"] is None
        assert f["about_or_bio"] is None
        assert f["recent_posts"] == [] and f["topics"] == [] and f["other_visible_facts"] == []

    @pytest.mark.asyncio
    async def test_wall_is_blocked_not_data(self, visual_enabled):
        trace = _trace("stopped_wall", [], landing_status=999,
                       reason="landing page classified login_wall: ['http_status_999']")
        with _patched(_fake_loop(trace, html=LOGIN_WALL_HTML)):
            result = await lvf.LakeVisualFetcher().fetch(URL)

        assert result.blocked is True and result.captcha_detected is False
        assert result.status_code == 999
        assert result.facts["status"] == "blocked"
        assert result.facts["facts"]["name"] is None
        assert result.facts["facts"]["recent_posts"] == []
        assert result.screenshot_bytes is None

    @pytest.mark.asyncio
    async def test_other_outcomes_are_partial(self, visual_enabled):
        trace = _trace("stopped_no_progress", [_step({"name": "Acme Corp"})],
                       reason="end of page reached, no posts section visible")
        with _patched(_fake_loop(trace)):
            result = await lvf.LakeVisualFetcher().fetch(URL)

        assert result.blocked is False
        assert result.facts["status"] == "partial"
        assert result.facts["facts"]["name"]["value"] == "Acme Corp"

    @pytest.mark.asyncio
    async def test_loop_exception_is_failed_never_raises(self, visual_enabled):
        trace = _trace("reached", [])
        with _patched(_fake_loop(trace, raise_exc=RuntimeError("browser died"))):
            result = await lvf.LakeVisualFetcher().fetch(URL)

        assert result.blocked is True
        assert result.facts["status"] == "failed"
        assert "RuntimeError: browser died" in result.facts["reason"]


class TestFixtureClassification:
    """The probe's own wall/CAPTCHA classifier against the synthetic fixtures."""

    def test_company_page_with_recaptcha_script_is_ok(self):
        loop, _ = lvf._probe_modules()
        outcome, signals = loop.probe_browser.classify(
            200, URL, "Acme Corp | Example Directory", COMPANY_HTML
        )
        assert outcome == "ok"
        assert "captcha_script_present_not_challenge" in signals

    def test_login_wall_fixture_is_login_wall(self):
        loop, _ = lvf._probe_modules()
        outcome, signals = loop.probe_browser.classify(
            999, "https://example.test/authwall?trk=x", "Sign Up | Example Directory",
            LOGIN_WALL_HTML,
        )
        assert outcome == "login_wall"
        assert "http_status_999" in signals and "login_wall_url" in signals
