"""How many protected pages does LakePlaywrightFetcher get, and how?

Runs the real fetcher (normal browser + automatic stealth retry) on each URL in
refusal_urls.txt and prints whether the page was fetched, the status, the time,
and which browser got it (normal browser, invisible_firefox or scrapling).
Redis sessions are stubbed out, so no services are needed. Only numbers and
page titles are printed; nothing scraped is stored.

    python benchmarks/refusal_bench.py
    python benchmarks/refusal_bench.py --urls my_urls.txt --no-stealth   # baseline
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("JWT_SECRET", "benchmark-only")

from src.config.settings import get_settings  # noqa: E402
from src.scraping.fetcher import lake_playwright_fetcher as mod  # noqa: E402
from src.scraping.fetcher import stealth_fallback as sf  # noqa: E402

DEFAULT_URLS = Path(__file__).with_name("refusal_urls.txt")


def read_urls(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


async def run(urls: list[str], stealth: bool) -> int:
    settings = get_settings().model_copy(update={"playwright_stealth_fallback": stealth})
    mod.get_settings = lambda: settings

    engine_used: dict[str, str] = {}
    original = sf.rescue

    async def spy(url, s, **kw):
        r = await original(url, s, **kw)
        engine_used[url] = r.engine if r else "all refused"
        return r

    mod.stealth_fallback.rescue = spy

    async def _none(*_a, **_k):
        return None

    got_total = 0
    for url in urls:
        fetcher = mod.LakePlaywrightFetcher()
        fetcher._get_redis_client = _none
        fetcher._load_session = _none
        fetcher._save_session = _none
        start = time.time()
        r = await fetcher.fetch(url)
        m = re.search(r"<title[^>]*>(.*?)</title>", r.html, re.S | re.I)
        title = re.sub(r"\s+", " ", m.group(1)).strip()[:45] if m else ""
        refused = sf.looks_refused(r.status_code, r.html)
        got = not r.blocked and not r.captcha_detected and not refused
        got_total += got
        how = engine_used.get(url, "normal browser")
        print(
            f"{'GOT PAGE' if got else 'REFUSED '} {r.status_code:3} {time.time() - start:6.1f}s"
            f" via {how:17} {url[:60]:60} {title}",
            flush=True,
        )
    print(f"TOTAL {got_total}/{len(urls)} pages fetched", flush=True)
    return got_total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--urls", type=Path, default=DEFAULT_URLS)
    ap.add_argument("--no-stealth", action="store_true", help="normal browser only (baseline)")
    a = ap.parse_args()
    asyncio.run(run(read_urls(a.urls), stealth=not a.no_stealth))
    return 0


if __name__ == "__main__":
    sys.exit(main())
