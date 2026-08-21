"""Fetch + detect core shared by the single-URL and batch Tech Detect paths.

Deliberately httpx-first. `LakePlaywrightFetcher` returns an empty `headers`
dict, which silently disables every header-based signature (1,648 of them), and
is 5-12x slower. Playwright is used only to escalate past a block, and even then
the httpx headers are carried forward.

Detection itself (`TechParser` ~1s + Wappalyzer ~1.6s per page) is CPU-bound and
GIL-held, so it runs in a spawn-context process pool rather than on the caller's
event loop.
"""

from __future__ import annotations

import asyncio
import ipaddress
import multiprocessing
import socket
import time
from concurrent.futures import ProcessPoolExecutor
from urllib.parse import urlparse

import httpx
import structlog

log = structlog.get_logger()

FETCH_TIMEOUT = 20.0
MAX_HTML_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 5
MIN_HTML_BYTES = 20

BLOCKED_STATUSES = frozenset({401, 403, 405, 406, 429, 503})

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

STATUS_OK = "ok"
STATUS_BLOCKED = "blocked"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"


class UnsafeURLError(ValueError):
    """Raised when a URL resolves somewhere we refuse to fetch."""


class ResolveError(UnsafeURLError):
    """Raised when a host cannot be resolved at all (not a safety refusal)."""


def clean_host(raw: str) -> str:
    host = raw.strip()
    for scheme in ("https://", "http://"):
        if host.startswith(scheme):
            host = host[len(scheme):]
    host = host.split("/")[0].split(":")[0].strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host.lower()


def normalize_input(raw: str) -> tuple[str, bool]:
    """Return (url, had_explicit_path).

    A bare domain may be retried across scheme/www variants; a URL the user
    typed with a path is fetched as given so we never silently detect a
    different page than the one they asked for.
    """
    value = (raw or "").strip()
    if not value:
        raise ValueError("empty url")
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    parsed = urlparse(value)
    if not parsed.netloc:
        raise ValueError("invalid url")
    has_path = bool(parsed.path.strip("/")) or bool(parsed.query)
    return value, has_path


def url_variants(raw: str) -> list[str]:
    host = clean_host(raw)
    return [
        f"https://{host}",
        f"https://www.{host}",
        f"http://{host}",
        f"http://www.{host}",
    ]


def _ip_is_public(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_safe_url(url: str) -> None:
    """Reject non-HTTP schemes and hosts that resolve to internal addresses.

    Tech Detect is a new user-controlled server-side fetch surface, so without
    this `http://169.254.169.254/` or `http://localhost:3001/` would be
    reachable from inside the network.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError(f"Unsupported scheme: {parsed.scheme or 'none'}")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ResolveError(f"Could not resolve host '{host}'") from e
    addrs = {i[4][0] for i in infos}
    if not addrs:
        raise UnsafeURLError("Host did not resolve")
    for addr in addrs:
        if not _ip_is_public(addr):
            raise UnsafeURLError(f"Host resolves to a non-public address ({addr})")


async def _get_once(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """Single GET with manual redirect walking, re-checking each hop for SSRF."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        await asyncio.to_thread(assert_safe_url, current)
        resp = await client.get(
            current,
            headers=FETCH_HEADERS,
            follow_redirects=False,
            timeout=FETCH_TIMEOUT,
        )
        if resp.is_redirect:
            location = resp.headers.get("location")
            if not location:
                return resp
            current = str(resp.url.join(location))
            continue
        return resp
    raise UnsafeURLError("Too many redirects")


def _looks_like_html(resp: httpx.Response) -> bool:
    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not ctype:
        return True
    return ctype in (
        "text/html", "application/xhtml+xml", "text/plain",
        "application/xml", "text/xml",
    )


async def _read_capped(resp: httpx.Response) -> tuple[str, bool]:
    body = resp.content
    truncated = len(body) > MAX_HTML_BYTES
    if truncated:
        body = body[:MAX_HTML_BYTES]
    encoding = resp.encoding or "utf-8"
    try:
        return body.decode(encoding, errors="replace"), truncated
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace"), truncated


async def fetch_page(url: str) -> dict:
    """Fetch one page via httpx, walking scheme/www variants for bare domains.

    Returns a dict with html/headers/status/http_status/final_url/error.
    Never raises for network conditions — those become a `failed` status.
    """
    try:
        primary, has_path = normalize_input(url)
    except ValueError as e:
        return {"status": STATUS_FAILED, "error": str(e), "html": "", "headers": {},
                "http_status": None, "final_url": url, "truncated": False}

    candidates = [primary] if has_path else url_variants(primary)
    last_error = ""
    limits = httpx.Limits(max_connections=10)

    async with httpx.AsyncClient(limits=limits, verify=True) as client, \
               httpx.AsyncClient(limits=limits, verify=False) as insecure:
        for candidate in candidates:
            for attempt_client in (client, insecure):
                try:
                    resp = await _get_once(attempt_client, candidate)
                except ResolveError as e:
                    last_error = str(e)
                    break
                except UnsafeURLError as e:
                    return {"status": STATUS_FAILED, "error": f"Blocked for safety: {e}",
                            "html": "", "headers": {}, "http_status": None,
                            "final_url": candidate, "truncated": False}
                except Exception as e:  # noqa: BLE001 - network conditions are data here
                    last_error = repr(e)
                    if attempt_client is client and "CERTIFICATE_VERIFY_FAILED" in last_error:
                        continue
                    break

                headers = {str(k): str(v) for k, v in resp.headers.items()}
                final_url = str(resp.url)

                if not _looks_like_html(resp):
                    ctype = resp.headers.get("content-type", "unknown")
                    return {"status": STATUS_SKIPPED, "error": f"Not an HTML page ({ctype})",
                            "html": "", "headers": headers, "http_status": resp.status_code,
                            "final_url": final_url, "truncated": False}

                html, truncated = await _read_capped(resp)
                blocked = (
                    resp.status_code in BLOCKED_STATUSES
                    or len(html) < MIN_HTML_BYTES
                    or _captcha(html)
                )
                return {
                    "status": STATUS_BLOCKED if blocked else STATUS_OK,
                    "error": f"Blocked or challenged (HTTP {resp.status_code})" if blocked else "",
                    "html": html,
                    "headers": headers,
                    "http_status": resp.status_code,
                    "final_url": final_url,
                    "truncated": truncated,
                }

    return {"status": STATUS_FAILED, "error": last_error or "Could not fetch page",
            "html": "", "headers": {}, "http_status": None, "final_url": primary,
            "truncated": False}


def _captcha(html: str) -> bool:
    from src.scraping.fetcher.captcha_detector import detect_captcha

    try:
        return detect_captcha(html)
    except Exception:  # noqa: BLE001
        return False


async def escalate_with_browser(url: str, httpx_headers: dict) -> dict:
    """Retry a blocked page through Playwright, keeping the httpx headers.

    Playwright returns no response headers, so without carrying the httpx ones
    forward every header-based signature would go dark on escalated pages.
    """
    from src.models.scraping import FetchOptions, ScrapingTier
    from src.scraping.fetcher.factory import create_fetcher

    try:
        fetcher = create_fetcher(ScrapingTier.PLAYWRIGHT)
        result = await fetcher.fetch(url, FetchOptions())
    except Exception as e:  # noqa: BLE001
        return {"status": STATUS_BLOCKED, "error": f"Escalation failed: {e!r}",
                "html": "", "headers": httpx_headers, "http_status": None,
                "final_url": url, "truncated": False}

    html = getattr(result, "html", "") or ""
    merged = dict(httpx_headers)
    for k, v in (getattr(result, "headers", None) or {}).items():
        merged[str(k)] = str(v)

    blocked = getattr(result, "blocked", False) or len(html) < MIN_HTML_BYTES or _captcha(html)
    return {
        "status": STATUS_BLOCKED if blocked else STATUS_OK,
        "error": "Blocked even after browser escalation" if blocked else "",
        "html": html,
        "headers": merged,
        "http_status": getattr(result, "status_code", None),
        "final_url": url,
        "truncated": False,
        "escalated": True,
    }


def merge_wappalyzer_detections(detected: dict, wapp_full: dict[str, list[str]]) -> None:
    """Merge Wappalyzer-library-only findings into a TechParser result, in place.

    Single implementation, imported by both ContentWorker and Tech Detect.
    """
    from src.scraping.parser.wappalyzer_runner import wapp_by_column

    existing_names = {d["name"].lower() for d in detected["detections"]}

    for col, names in wapp_by_column(wapp_full).items():
        if col in detected and isinstance(detected[col], list):
            for name in names:
                if name.lower() not in existing_names and name not in detected[col]:
                    detected[col].append(name)

    for name, categories in wapp_full.items():
        if name.lower() in existing_names:
            continue
        existing_names.add(name.lower())
        detected["detections"].append({
            "name": name,
            "category": categories[0] if categories else "other",
            "confidence": "medium",
            "evidence": "wappalyzer library match",
            "evidence_type": "wappalyzer",
            "recommended": True,
        })


def detect_sync(url: str, html: str, headers: dict, wappalyzer: bool) -> dict:
    """Pure CPU detection. Module-level and picklable so it can run in a pool."""
    from src.scraping.parser.tech_parser import TechParser

    detected = TechParser(html, headers).detect()
    if wappalyzer:
        from src.scraping.parser.wappalyzer_runner import detect_wappalyzer_full

        try:
            wapp_full = detect_wappalyzer_full(url, html, headers)
            if wapp_full:
                merge_wappalyzer_detections(detected, wapp_full)
        except Exception:  # noqa: BLE001 - never let the add-on fail the detect
            pass
    return detected


_POOL: ProcessPoolExecutor | None = None
_POOL_LOCK = asyncio.Lock()


async def get_pool(max_workers: int = 2) -> ProcessPoolExecutor:
    """Lazily create the spawn-context detection pool.

    `spawn` is required: the default `fork` on Linux would hand children a copy
    of the parent's asyncpg pool and redis sockets. Pooled work is pure
    (url, html, headers) -> dict, so children never touch the DB.
    """
    global _POOL
    async with _POOL_LOCK:
        if _POOL is None:
            from src.scraping.parser.wappalyzer_runner import wapp_init

            _POOL = ProcessPoolExecutor(
                max_workers=max_workers,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=wapp_init,
            )
        return _POOL


def shutdown_pool() -> None:
    global _POOL
    if _POOL is not None:
        _POOL.shutdown(wait=False, cancel_futures=True)
        _POOL = None


async def run_detection(url: str, html: str, headers: dict, wappalyzer: bool,
                        use_pool: bool = True, max_workers: int = 2) -> dict:
    if not use_pool:
        return await asyncio.to_thread(detect_sync, url, html, headers, wappalyzer)
    pool = await get_pool(max_workers)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(pool, detect_sync, url, html, headers, wappalyzer)


async def detect_one(
    url: str,
    *,
    wappalyzer: bool = True,
    allow_escalation: bool = True,
    use_pool: bool = True,
    max_workers: int = 2,
) -> dict:
    """Fetch one URL and detect its tech stack.

    Always returns a result dict; network and parse problems become a status,
    never an exception.
    """
    started = time.monotonic()
    fetched = await fetch_page(url)

    if fetched["status"] == STATUS_BLOCKED and allow_escalation:
        escalated = await escalate_with_browser(fetched["final_url"], fetched["headers"])
        if escalated["status"] == STATUS_OK or escalated["html"]:
            fetched = escalated

    base = {
        "input_url": url,
        "final_url": fetched["final_url"],
        "domain": clean_host(fetched["final_url"] or url),
        "status": fetched["status"],
        "http_status": fetched["http_status"],
        "error": fetched["error"],
        "escalated": fetched.get("escalated", False),
        "truncated": fetched.get("truncated", False),
        "detections": [],
        "by_category": {},
        "fields": {},
        "total": 0,
        "recommended": 0,
        "duration_ms": 0,
    }

    if fetched["status"] != STATUS_OK or not fetched["html"]:
        base["duration_ms"] = int((time.monotonic() - started) * 1000)
        return base

    try:
        detected = await run_detection(
            fetched["final_url"], fetched["html"], fetched["headers"],
            wappalyzer, use_pool=use_pool, max_workers=max_workers,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("tech_detect_parse_failed", url=url, error=str(e))
        base["status"] = STATUS_FAILED
        base["error"] = f"Detection failed: {e!r}"
        base["duration_ms"] = int((time.monotonic() - started) * 1000)
        return base

    from src.services.tech_categories import group_detections, normalize_category

    detections = detected.get("detections", [])
    for det in detections:
        det["category_raw"] = det.get("category")
        det["category_id"] = normalize_category(det.get("category"))

    base["detections"] = detections
    base["by_category"] = group_detections(detections)
    base["fields"] = {k: v for k, v in detected.items() if k != "detections"}
    base["total"] = len(detections)
    base["recommended"] = sum(1 for d in detections if d.get("recommended"))
    base["duration_ms"] = int((time.monotonic() - started) * 1000)
    return base
