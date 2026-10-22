"""Content change notifications (v2).

Records page-level changes and pushes a webhook notification, reusing the same
SSRF-safe HTTP-POST shape as webhook_export but returning bools (no HTTP
exceptions) so it can run inside the worker.
"""

import ipaddress
import socket
from urllib.parse import urlparse

import httpx
import structlog

log = structlog.get_logger()


def is_safe_webhook_url(url: str) -> bool:
    """True only for public http(s) URLs (SSRF guard, non-raising variant)."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(parsed.hostname))
    except (socket.gaierror, ValueError):
        return False
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast
    )


async def notify_content_change(
    webhook_url: str,
    *,
    domain: str,
    url: str,
    old_hash: str | None,
    new_hash: str,
) -> bool:
    """POST a change notification to a webhook. Returns True on 2xx/3xx."""
    if not is_safe_webhook_url(webhook_url):
        log.warning("change_webhook_blocked", webhook_url=webhook_url, url=url)
        return False

    payload = {
        "source": "lake_b2b_scraper",
        "trigger": "content_change",
        "domain": domain,
        "url": url,
        "old_hash": old_hash,
        "new_hash": new_hash,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                webhook_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Lake-B2B-Scraper/1.0",
                },
            )
            success = resp.status_code < 400
            log.info(
                "change_webhook_sent", url=url, webhook_url=webhook_url,
                status=resp.status_code, success=success,
            )
            return success
    except Exception:
        log.exception("change_webhook_failed", url=url, webhook_url=webhook_url)
        return False
