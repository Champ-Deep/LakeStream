"""Scripted browser actions for Playwright fetchers (v2).

Interprets a small, safe action list against a live Playwright page before
content is captured. Every action is best-effort: a failing action is logged
and skipped so it degrades to a normal fetch rather than failing the request.
"""

import structlog

log = structlog.get_logger()

_SUPPORTED = {"click", "scroll", "wait", "wait_for_selector", "screenshot"}


async def apply_pre_capture(page, options, default_timeout_ms: int) -> None:
    """Honor wait_for_selector and run the scripted action list, if any."""
    if options.wait_for_selector:
        try:
            await page.wait_for_selector(options.wait_for_selector, timeout=default_timeout_ms)
        except Exception as e:
            log.debug("wait_for_selector_timeout", selector=options.wait_for_selector, error=str(e))

    for action in options.actions or []:
        await _run_action(page, action, default_timeout_ms)


async def _run_action(page, action: dict, default_timeout_ms: int) -> None:
    atype = (action or {}).get("type")
    if atype not in _SUPPORTED:
        log.debug("unsupported_action_skipped", action=atype)
        return
    try:
        if atype == "click":
            await page.click(action["selector"], timeout=default_timeout_ms)
        elif atype == "wait_for_selector":
            await page.wait_for_selector(action["selector"], timeout=default_timeout_ms)
        elif atype == "wait":
            await page.wait_for_timeout(min(int(action.get("ms", 1000)), 30000))
        elif atype == "scroll":
            await page.evaluate(
                "(y) => window.scrollBy(0, y)", int(action.get("ms", 2000))
            )
        elif atype == "screenshot":
            # In-flow screenshots are handled by capture_screenshot; this is a no-op
            # placeholder so a screenshot step in an action list doesn't error.
            pass
    except Exception as e:
        log.debug("action_failed", action=atype, error=str(e))


async def capture_screenshot(page, enabled: bool) -> bytes | None:
    """Return a full-page PNG when enabled, else None. Never raises."""
    if not enabled:
        return None
    try:
        return await page.screenshot(full_page=True)
    except Exception as e:
        log.debug("screenshot_failed", error=str(e))
        return None
