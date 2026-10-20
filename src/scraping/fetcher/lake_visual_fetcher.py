"""Tier: visual (v2 probe). Screenshot-guided fetch that returns cited facts.

Optional path, off by default (``settings.enable_visual_fetcher``) and only
reachable through an explicit tier override (``"visual"``); it is never part
of the escalation chain. Wraps the probe in ``scripts/visual_probe``:
stealth browser -> vision observations -> Jev-chosen bounded actions ->
merged facts, each with source URL, capture time and screenshot.

Returns an ordinary :class:`FetchResult` (status, HTML, screenshot) plus
``facts`` carrying an explicit ``success`` / ``partial`` / ``blocked`` /
``failed`` status, so a wall is reported as a wall and never as data.

The probe modules still live under ``scripts/`` (the checklist asked for the
narrowest hook, not a move); promoting them to ``src/scraping/visual/`` is a
follow-up once the approach is accepted.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

import structlog

from src.config.constants import TIER_COSTS
from src.config.settings import get_settings
from src.models.scraping import FetchOptions, FetchResult, ScrapingTier
from src.scraping.fetcher.base import BaseFetcher

log = structlog.get_logger()

_PROBE_DIR = Path(__file__).resolve().parents[3] / "scripts" / "visual_probe"
# Loop outcome -> result status. Anything else (stopped_by_model,
# stopped_no_progress, budget_exhausted) is "partial": the page was reachable
# but the goal was not fully met.
_STATUS = {"reached": "success", "stopped_wall": "blocked", "error": "failed"}


def _probe_modules():
    """Import the probe package lazily; it is only needed when the tier runs."""
    if str(_PROBE_DIR) not in sys.path:
        sys.path.insert(0, str(_PROBE_DIR))
    import extract  # noqa: PLC0415
    import loop  # noqa: PLC0415

    return loop, extract


def _last_screenshot(trace: dict, out_dir: Path) -> bytes | None:
    for step in reversed(trace.get("steps") or []):
        for key in ("after", "before"):
            name = (step.get(key) or {}).get("png")
            if name and (out_dir / name).exists():
                return (out_dir / name).read_bytes()
    return None


class LakeVisualFetcher(BaseFetcher):
    """Screenshot-guided fetch with cited facts (flag-gated, override-only)."""

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        options = options or FetchOptions()
        settings = get_settings()
        start = time.time()

        if not settings.enable_visual_fetcher:
            return self._result(url, start, facts={
                "status": "failed",
                "reason": "visual fetcher disabled (enable_visual_fetcher=false)",
                "facts": None,
            })

        # The probe reads its keys from the environment (or the repo .env);
        # hand it the ones configured on the server without overriding a
        # value the operator already exported.
        if settings.openrouter_api_key:
            os.environ.setdefault("OPENROUTER_API_KEY", settings.openrouter_api_key)
        if settings.typesafe_api_key:
            os.environ.setdefault("TYPESAFE_API_KEY", settings.typesafe_api_key)

        out_dir = (
            Path(settings.screenshot_dir) / "visual"
            / hashlib.sha256(url.encode()).hexdigest()[:16]
            / time.strftime("%Y%m%d_%H%M%S")
        )
        html_sink: dict = {}
        try:
            loop, extract = _probe_modules()
            trace = await loop.run(
                url, out_dir, seed=None, headless=True,
                vision_model=settings.visual_vision_model,
                max_steps=settings.visual_max_steps,
                budget_s=settings.visual_budget_s,
                max_model_calls=settings.visual_max_model_calls,
                html_sink=html_sink,
            )
            facts = extract.merge_facts(trace)
        except Exception as exc:
            log.warning("visual_fetcher_error", url=url, error=str(exc),
                        error_type=type(exc).__name__)
            return self._result(url, start, facts={
                "status": "failed", "reason": f"{type(exc).__name__}: {exc}", "facts": None,
            })

        outcome = trace.get("outcome")
        status = _STATUS.get(outcome, "partial")
        landing = trace.get("landing") or {}
        reason = trace.get("reason") or trace.get("error")
        return self._result(
            url, start,
            status_code=int(landing.get("status") or 0),
            html=html_sink.get("html", ""),
            blocked=status == "blocked",
            captcha="captcha" in str(reason or ""),
            screenshot=_last_screenshot(trace, out_dir),
            model_cost=float(trace.get("vision_cost_usd") or 0.0),
            facts={
                "status": status,
                "loop_outcome": outcome,
                "reason": reason,
                "facts": facts,
                "steps": len(trace.get("steps") or []),
                "model_calls": trace.get("model_calls", 0),
                "vision_cost_usd": trace.get("vision_cost_usd", 0.0),
                "jev_input_tokens": trace.get("jev_tokens", 0),
                "evidence_dir": str(out_dir),
            },
        )

    @staticmethod
    def _result(url: str, start: float, *, facts: dict, status_code: int = 0, html: str = "",
                blocked: bool = True, captcha: bool = False, screenshot: bytes | None = None,
                model_cost: float = 0.0) -> FetchResult:
        facts.setdefault("producer", "visual_probe")
        return FetchResult(
            url=url,
            status_code=status_code,
            html=html,
            tier_used=ScrapingTier.VISUAL,
            cost_usd=TIER_COSTS["visual"] + model_cost,
            duration_ms=int((time.time() - start) * 1000),
            blocked=blocked,
            captcha_detected=captcha,
            screenshot_bytes=screenshot,
            facts=facts,
        )
