"""Visual-scraping probe, step 1: stealth browser -> screenshot + page record.

Launches invisible_playwright (anti-detect Firefox behind the Playwright API),
opens ONE permitted public URL, captures a viewport screenshot, a full-page
screenshot and the HTML, and writes a JSON record saying whether the page
loaded, was blocked, showed a CAPTCHA or hit a login wall.

A wall is reported, never worked around: no retries, no cookie reuse, no IP
cycling. The record is the evidence for checklist item L1.

Usage (repo root, inside .venv):
    python scripts/visual_probe/probe_browser.py https://example.com
    python scripts/visual_probe/probe_browser.py <url> --seed 42 --headed
    python scripts/visual_probe/probe_browser.py <url> --proxy-env

Env (only with --proxy-env, for the L4 direct-vs-proxy comparison):
    VISUAL_PROBE_PROXY_SERVER    e.g. http://host:port or socks5://host:port
    VISUAL_PROBE_PROXY_USERNAME
    VISUAL_PROBE_PROXY_PASSWORD
Credentials are read from the environment and never written to the record.

Exit codes: 0 page loaded, 2 blocked/captcha/login wall, 1 launch or navigation error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.scraping.fetcher.captcha_detector import detect_captcha  # noqa: E402

# Login-wall markers for logged-out public pages. Conservative on purpose:
# a false "ok" would be a fabricated result, a false "login_wall" is just a
# blocked row in the benchmark.
_LOGIN_WALL_URL = re.compile(r"/(authwall|login|checkpoint|uas/login|signup)\b", re.I)
_LOGIN_WALL_TITLE = re.compile(r"sign in|log in|join linkedin|sign up", re.I)
# Titles of real challenge/interstitial pages. CAPTCHA script markers alone are
# not enough: LinkedIn embeds an invisible reCAPTCHA in the sign-in form on
# every public page, including fully rendered company pages.
_CHALLENGE_TITLE = re.compile(
    r"just a moment|attention required|access denied|security verification|verify you are human",
    re.I,
)
_BLOCK_STATUSES = {403, 429, 503, 999}  # 999 is LinkedIn's bot-block status
_MIN_HTML_BYTES = 2000


def _proxy_from_env() -> dict[str, str] | None:
    server = os.environ.get("VISUAL_PROBE_PROXY_SERVER")
    if not server:
        return None
    proxy = {"server": server}
    if os.environ.get("VISUAL_PROBE_PROXY_USERNAME"):
        proxy["username"] = os.environ["VISUAL_PROBE_PROXY_USERNAME"]
        proxy["password"] = os.environ.get("VISUAL_PROBE_PROXY_PASSWORD", "")
    return proxy


def _versions() -> dict[str, str]:
    import invisible_core
    import invisible_playwright

    return {
        "python": sys.version.split()[0],
        "invisible_playwright": getattr(invisible_playwright, "__version__", "?"),
        "invisible_core": getattr(invisible_core, "__version__", "?"),
    }


def classify(status: int, final_url: str, title: str, html: str) -> tuple[str, list[str]]:
    """Return (outcome, signals). Outcome is one of ok/blocked/captcha/login_wall."""
    signals: list[str] = []
    if status in _BLOCK_STATUSES:
        signals.append(f"http_status_{status}")
    if len(html) < _MIN_HTML_BYTES:
        signals.append(f"tiny_html_{len(html)}b")
    captcha_markers = detect_captcha(html)
    challenge_title = bool(_CHALLENGE_TITLE.search(title or ""))
    if challenge_title:
        signals.append("challenge_title")
    if _LOGIN_WALL_URL.search(urlparse(final_url).path):
        signals.append("login_wall_url")
    if _LOGIN_WALL_TITLE.search(title or ""):
        signals.append("login_wall_title")

    hard_block = any(s.startswith(("http_status", "tiny_html")) for s in signals)
    if captcha_markers and (challenge_title or hard_block):
        signals.append("captcha_markers")
        return "captcha", signals
    if captcha_markers:
        # Embedded widget on an otherwise rendered page (LinkedIn sign-in form).
        signals.append("captcha_script_present_not_challenge")
    if any(s.startswith("login_wall") for s in signals):
        return "login_wall", signals
    if hard_block or challenge_title:
        return "blocked", signals
    return "ok", signals


async def run(url: str, out_dir: Path, *, seed: int | None, headless: bool,
              timezone: str, locale: str, proxy: dict[str, str] | None,
              timeout_ms: int) -> dict:
    from invisible_playwright.async_api import InvisiblePlaywright

    out_dir.mkdir(parents=True, exist_ok=True)
    record: dict = {
        "url": url,
        "captured_at": datetime.now(UTC).isoformat(),
        "versions": _versions(),
        "headless": headless,
        "proxy_used": bool(proxy),
        "proxy_scheme": urlparse(proxy["server"]).scheme if proxy else None,
        "timings_ms": {},
    }
    t0 = time.time()
    ip = InvisiblePlaywright(seed=seed, headless=headless, proxy=proxy,
                             timezone=timezone, locale=locale)
    try:
        async with ip as browser:
            record["seed"] = ip.seed
            record["timings_ms"]["launch"] = int((time.time() - t0) * 1000)

            page = await browser.new_page()
            t1 = time.time()
            response = await page.goto(url, timeout=timeout_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                record["networkidle_timeout"] = True
            record["timings_ms"]["goto"] = int((time.time() - t1) * 1000)

            status = response.status if response else 0
            final_url = page.url
            title = await page.title()
            html = await page.content()
            record.update({
                "status": status,
                "final_url": final_url,
                "title": title,
                "user_agent": await page.evaluate("navigator.userAgent"),
                "html_bytes": len(html),
            })

            parsed = urlparse(url)
            stem = re.sub(r"[^a-z0-9]+", "_", parsed.netloc + parsed.path, flags=re.I)
            stem = stem.strip("_")[:80]
            viewport_png = out_dir / f"{stem}.viewport.png"
            full_png = out_dir / f"{stem}.full.png"
            html_path = out_dir / f"{stem}.html"
            await page.screenshot(path=str(viewport_png))
            await page.screenshot(path=str(full_png), full_page=True)
            html_path.write_text(html, encoding="utf-8")
            record["files"] = {
                "viewport_png": str(viewport_png),
                "full_png": str(full_png),
                "html": str(html_path),
            }

            outcome, signals = classify(status, final_url, title, html)
            record["outcome"] = outcome
            record["signals"] = signals
    except Exception as exc:
        record["outcome"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["timings_ms"]["total"] = int((time.time() - t0) * 1000)
    return record


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("url")
    ap.add_argument("--out", default=str(REPO_ROOT / "data" / "visual_probe"),
                    help="output root; a timestamped folder is created inside it")
    ap.add_argument("--seed", type=int, default=None,
                    help="fingerprint seed (same seed = same profile)")
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    ap.add_argument("--timezone", default="", help='IANA zone; "" = follow egress IP (default)')
    ap.add_argument("--locale", default="auto")
    ap.add_argument("--proxy-env", action="store_true", help="use VISUAL_PROBE_PROXY_* env vars")
    ap.add_argument("--timeout-ms", type=int, default=60_000)
    args = ap.parse_args()

    proxy = _proxy_from_env() if args.proxy_env else None
    if args.proxy_env and not proxy:
        print("--proxy-env given but VISUAL_PROBE_PROXY_SERVER is not set", file=sys.stderr)
        return 1

    run_dir = Path(args.out) / datetime.now().strftime("%Y%m%d_%H%M%S")
    record = asyncio.run(run(
        args.url, run_dir, seed=args.seed, headless=not args.headed,
        timezone=args.timezone, locale=args.locale, proxy=proxy, timeout_ms=args.timeout_ms,
    ))
    record_path = run_dir / "browser_record.json"
    run_dir.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    print(json.dumps({k: v for k, v in record.items() if k != "files"}, indent=2))
    print(f"\nrecord: {record_path}")
    return {"ok": 0, "error": 1}.get(record["outcome"], 2)


if __name__ == "__main__":
    sys.exit(main())
