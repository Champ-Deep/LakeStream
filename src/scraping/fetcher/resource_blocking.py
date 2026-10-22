"""Drop browser requests that cost bandwidth and add nothing to extraction.

Used by the Playwright fetchers (tiers 2.5 and 3). Behind the
``playwright_block_resources`` setting (on by default).

What is blocked
---------------
* Resource types that never change the DOM we parse: ``image``, ``media``
  (video / audio), ``font``, and ``ping`` (sendBeacon / ``<a ping>``).
  ``<img>`` / ``<video>`` tags stay in the HTML; only their bytes are skipped.
* Any request to a known ad / analytics / tracking host (and its subdomains),
  whatever its type.

What is never blocked
---------------------
* The page's own top-level navigation, even if the site itself is on the list.
* First-party and third-party scripts, XHR / fetch, stylesheets, documents
  (except ad hosts): these build the DOM that ``pricing_parser`` /
  ``contact_parser`` read, so blocking them could change what we extract.
* Images and fonts when a screenshot is requested (the visual tier needs them).

Allowed requests call ``route.fallback()`` rather than ``route.continue_()``, so
any other route handlers registered on the page or context still run.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import structlog

log = structlog.get_logger()

# Resource types whose bytes never reach the DOM.
BLOCKED_RESOURCE_TYPES: frozenset[str] = frozenset({"image", "media", "font", "ping"})

# Visual types that a screenshot needs.
VISUAL_RESOURCE_TYPES: frozenset[str] = frozenset({"image", "media", "font"})

# Ad, analytics and tracking hosts. A request is blocked when its host equals one
# of these or is a subdomain of one. Chat widgets, consent managers, form
# providers and CDNs are deliberately NOT here: they can carry page content.
TRACKER_HOSTS: frozenset[str] = frozenset(
    {
        # Google ads + analytics
        "google-analytics.com",
        "analytics.google.com",
        "googletagmanager.com",
        "googletagservices.com",
        "doubleclick.net",
        "googlesyndication.com",
        "googleadservices.com",
        "adservice.google.com",
        "pagead2.googlesyndication.com",
        # Meta, Microsoft, LinkedIn, X, TikTok, Pinterest, Reddit, Snap, Quora ad pixels
        "connect.facebook.net",
        "clarity.ms",
        "bat.bing.com",
        "snap.licdn.com",
        "px.ads.linkedin.com",
        "ads.linkedin.com",
        "static.ads-twitter.com",
        "ads-api.twitter.com",
        "analytics.twitter.com",
        "analytics.tiktok.com",
        "ct.pinterest.com",
        "alb.reddit.com",
        "sc-static.net",
        "tr.snapchat.com",
        "q.quora.com",
        # Product analytics / session replay
        "hotjar.com",
        "hotjar.io",
        "fullstory.com",
        "mixpanel.com",
        "mxpnl.com",
        "amplitude.com",
        "segment.com",
        "segment.io",
        "heap.io",
        "heapanalytics.com",
        "mouseflow.com",
        "crazyegg.com",
        "luckyorange.com",
        "luckyorange.net",
        "smartlook.com",
        "inspectlet.com",
        "logrocket.io",
        "lr-ingest.io",
        "posthog.com",
        "plausible.io",
        "matomo.cloud",
        "statcounter.com",
        "quantserve.com",
        "scorecardresearch.com",
        "chartbeat.com",
        "chartbeat.net",
        # B2B intent / reverse-IP trackers
        "hs-analytics.net",
        "hsadspixel.net",
        "hscollectedforms.net",
        "6sc.co",
        "6sense.com",
        "demandbase.com",
        "bizible.com",
        "clearbit.com",
        "clearbitscripts.com",
        "leadfeeder.com",
        "lfeeder.com",
        "albacross.com",
        "ws.zoominfo.com",
        "munchkin.marketo.net",
        "rlcdn.com",
        # Ad networks / exchanges
        "adnxs.com",
        "adsrvr.org",
        "amazon-adsystem.com",
        "criteo.com",
        "criteo.net",
        "taboola.com",
        "outbrain.com",
        "rubiconproject.com",
        "pubmatic.com",
        "openx.net",
        "casalemedia.com",
        "moatads.com",
        "adroll.com",
        "media.net",
        "yieldmo.com",
        "sharethrough.com",
        "bidswitch.net",
        "teads.tv",
        "smartadserver.com",
        "demdex.net",
        "everesttech.net",
        # Monitoring beacons (no content)
        "nr-data.net",
        "bam.nr-data.net",
        "browser-intake-datadoghq.com",
    }
)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def matched_tracker(host: str, tracker_hosts: frozenset[str] | None = None) -> str | None:
    """The listed tracker domain that ``host`` equals or is a subdomain of, else None."""
    tracker_hosts = TRACKER_HOSTS if tracker_hosts is None else tracker_hosts
    host = host.lower().rstrip(".")
    if not host:
        return None
    parts = host.split(".")
    # check host, then each parent domain: a.b.example.com -> b.example.com -> example.com
    for i in range(len(parts)):
        candidate = ".".join(parts[i:])
        if candidate in tracker_hosts:
            return candidate
    return None


def is_tracker_host(host: str, tracker_hosts: frozenset[str] | None = None) -> bool:
    """True if ``host`` is a listed tracker or a subdomain of one."""
    return matched_tracker(host, tracker_hosts) is not None


# Second-level labels under two-letter country TLDs (acme.co.uk, acme.com.au ...).
_COUNTRY_SLDS = frozenset({"co", "com", "org", "net", "ac", "gov", "edu", "ltd", "plc", "ne", "or"})


def site_of(host: str) -> str:
    """Registrable domain, approximately: www.zoominfo.com -> zoominfo.com,
    shop.acme.co.uk -> acme.co.uk. IP addresses and single labels are returned as is."""
    host = host.lower().rstrip(".")
    parts = host.split(".")
    if len(parts) <= 2 or parts[-1].isdigit():
        return host
    if len(parts[-1]) == 2 and parts[-2] in _COUNTRY_SLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _same_site(page_host: str, request_host: str) -> bool:
    return site_of(page_host) == site_of(request_host)


def block_reason(
    url: str,
    resource_type: str,
    *,
    is_main_document: bool = False,
    keep_visuals: bool = False,
    page_host: str = "",
    tracker_hosts: frozenset[str] | None = None,
) -> str | None:
    """Why this request should be dropped, or ``None`` to let it through.

    ``page_host`` is the host of the page being scraped. A tracker vendor's own
    site (for example scraping amplitude.com) is first-party there, so its
    requests are not treated as tracking.
    """
    if is_main_document:
        return None
    req_host = _host(url)
    tracker = matched_tracker(req_host, tracker_hosts)
    if tracker and not (page_host and _same_site(page_host, req_host)):
        return "tracker"
    if resource_type in BLOCKED_RESOURCE_TYPES:
        if keep_visuals and resource_type in VISUAL_RESOURCE_TYPES:
            return None
        return resource_type
    return None


@dataclass
class BlockStats:
    """Counts of what the route handler did on one page."""

    allowed: int = 0
    blocked: int = 0
    by_reason: Counter = field(default_factory=Counter)

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "blocked": self.blocked, "by_reason": dict(self.by_reason)}


async def install_resource_blocking(
    target,
    *,
    keep_visuals: bool = False,
    tracker_hosts: frozenset[str] | None = None,
) -> BlockStats:
    """Register the blocking route on a Playwright ``Page`` or ``BrowserContext``.

    Returns a live ``BlockStats`` that fills in as the page loads.
    """
    hosts = TRACKER_HOSTS if tracker_hosts is None else tracker_hosts  # read at install time
    stats = BlockStats()
    page_host = {"value": ""}  # host of the latest top-level navigation (follows redirects)

    async def _handle(route, request) -> None:
        try:
            frame = request.frame
            is_main = request.is_navigation_request() and frame.parent_frame is None
        except Exception:  # service-worker requests have no frame
            is_main = False
        if is_main:
            page_host["value"] = _host(request.url)
        reason = block_reason(
            request.url,
            request.resource_type,
            is_main_document=is_main,
            keep_visuals=keep_visuals,
            page_host=page_host["value"],
            tracker_hosts=hosts,
        )
        if reason is None:
            stats.allowed += 1
            await route.fallback()
            return
        stats.blocked += 1
        stats.by_reason[reason] += 1
        await route.abort("blockedbyclient")

    await target.route("**/*", _handle)
    return stats


async def maybe_install_resource_blocking(page, settings, options=None) -> BlockStats | None:
    """Install blocking on ``page`` if the setting is on; keep visuals for screenshots."""
    if not getattr(settings, "playwright_block_resources", True):
        return None
    keep_visuals = bool(options is not None and getattr(options, "capture_screenshot", False))
    return await install_resource_blocking(page, keep_visuals=keep_visuals)


def log_block_stats(stats: BlockStats | None, url: str, domain: str) -> None:
    if stats is not None:
        log.debug("playwright_resource_blocking", url=url, domain=domain, **stats.as_dict())
