"""Company/brand enrichment — domain/email/name → cached firmographic profile.

Self-contained (no external enrichment vendor):
  1. Resolve the domain (directly, from an email, or via LakeCurrent search).
  2. Serve from the company_profiles cache unless force_refresh.
  3. Fetch homepage (+ /about when reachable) with the cheapest available tier.
  4. Pull logo/description/socials deterministically from HTML meta tags.
  5. Ask the LLM for firmographics, constraining industry to the
     LAKE_B2B_INDUSTRIES taxonomy; degrade to meta-only when AI is off.
  6. Map industry → NAICS/SIC via the deterministic crosswalk.
"""

import asyncio
import ipaddress
import json
import re
import socket
from urllib.parse import urljoin, urlparse
from uuid import UUID

import structlog
from asyncpg import Pool
from selectolax.parser import HTMLParser

from src.config.settings import get_settings
from src.db.queries.company_profiles import get_by_domain, upsert_company_profile
from src.models.company import CompanyProfile
from src.models.lake_b2b import LAKE_B2B_INDUSTRIES
from src.services.dns_intel import lookup_domain_intel
from src.services.naics_crosswalk import industry_to_codes
from src.services.ssl_intel import inspect_certificate

log = structlog.get_logger()

_SOCIAL_HOSTS = {
    "linkedin.com": "linkedin",
    "twitter.com": "twitter",
    "x.com": "twitter",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "youtube.com": "youtube",
    "github.com": "github",
}

_EMPLOYEE_RANGES = ["1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5001-10000", "10000+"]
_REVENUE_RANGES = ["<$1M", "$1M-$10M", "$10M-$50M", "$50M-$100M", "$100M-$500M", "$500M-$1B", "$1B+"]


class EnrichmentError(Exception):
    """Raised when a company cannot be resolved or fetched."""


def _normalize_domain(value: str) -> str:
    d = value.strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = d.split("/")[0].split(":")[0]
    return d.removeprefix("www.")


def _domain_is_safe(domain: str) -> bool:
    """Reject domains that resolve to private/loopback ranges (SSRF guard)."""
    try:
        infos = socket.getaddrinfo(domain, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    return bool(infos)


async def _resolve_domain(
    *, domain: str | None, email: str | None, name: str | None
) -> str:
    if domain:
        return _normalize_domain(domain)
    if email and "@" in email:
        host = email.rsplit("@", 1)[1].strip().lower()
        if "." not in host:
            raise EnrichmentError(f"Invalid email domain: {host}")
        return _normalize_domain(host)
    if name:
        settings = get_settings()
        if not settings.lakecurrent_enabled:
            raise EnrichmentError("Name resolution needs the search backend (disabled)")
        from src.services.lakecurrent import LakeCurrentClient

        client = LakeCurrentClient(settings.lakecurrent_base_url, settings.lakecurrent_timeout)
        try:
            resp = await client.search(f'"{name}" official website', limit=5)
        finally:
            await client.close()
        for r in resp.results:
            if r.domain:
                return _normalize_domain(r.domain)
        raise EnrichmentError(f"No domain found for company name: {name}")
    raise EnrichmentError("Provide one of: domain, email, company_name")


async def _fetch_page(url: str) -> str:
    """Fetch one page's HTML with the cheapest configured tier; '' on failure."""
    from src.models.scraping import FetchOptions, ScrapingTier
    from src.scraping.fetcher.factory import create_fetcher

    settings = get_settings()
    tier = (
        ScrapingTier.GO_HTTP
        if settings.enable_go_fetchers and settings.go_http_fetcher_url
        else ScrapingTier.PLAYWRIGHT
    )
    try:
        fetcher = create_fetcher(tier)
        result = await fetcher.fetch(url, FetchOptions(tier=tier))
        if result.html and not result.blocked:
            return result.html
    except Exception as e:
        log.debug("enrich_fetch_failed", url=url, error=str(e))
    return ""


def _meta_from_html(html: str, base_url: str) -> dict:
    """Deterministic extraction: name, description, logo, socials."""
    out: dict = {"socials": {}}
    tree = HTMLParser(html)

    def meta(prop: str, attr: str = "property") -> str | None:
        node = tree.css_first(f'meta[{attr}="{prop}"]')
        return node.attributes.get("content") if node else None

    out["name"] = meta("og:site_name") or (
        tree.css_first("title").text(strip=True) if tree.css_first("title") else None
    )
    out["description"] = (
        meta("og:description") or meta("description", attr="name") or None
    )

    logo = meta("og:image")
    if not logo:
        for sel in ('link[rel="apple-touch-icon"]', 'link[rel~="icon"]'):
            node = tree.css_first(sel)
            if node and node.attributes.get("href"):
                logo = node.attributes["href"]
                break
    out["logo_url"] = urljoin(base_url, logo) if logo else None

    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        host = urlparse(href).netloc.lower().removeprefix("www.")
        for social_host, key in _SOCIAL_HOSTS.items():
            if host == social_host or host.endswith("." + social_host):
                out["socials"].setdefault(key, href)
    return out


async def _llm_firmographics(text: str, domain: str, org_id: UUID | None) -> dict:
    """LLM pass for industry/employee/revenue/location; {} when AI unavailable."""
    from src.services.llm_extractor import LLMExtractor, get_openrouter_config

    try:
        await get_openrouter_config(org_id)
    except ValueError:
        return {}

    prompt = (
        f"From this company website content ({domain}), extract firmographics as JSON with keys: "
        f'"company_name" (official name), '
        f'"industry" (EXACTLY one of: {json.dumps(LAKE_B2B_INDUSTRIES)}), '
        f'"employee_range" (one of: {json.dumps(_EMPLOYEE_RANGES)} or null), '
        f'"revenue_range" (one of: {json.dumps(_REVENUE_RANGES)} or null), '
        f'"location" (HQ city + country or null), '
        f'"description" (one factual sentence). '
        f"Use null when the content does not support an answer. Never invent numbers."
    )
    try:
        data = await LLMExtractor(org_id=org_id).extract_freeform(text[:25000], prompt)
    except Exception as e:
        log.warning("enrich_llm_failed", domain=domain, error=str(e))
        return {}
    if not isinstance(data, dict) or data.get("_extraction_errors"):
        return {}
    if data.get("industry") not in LAKE_B2B_INDUSTRIES:
        data["industry"] = None
    if data.get("employee_range") not in _EMPLOYEE_RANGES:
        data["employee_range"] = None
    if data.get("revenue_range") not in _REVENUE_RANGES:
        data["revenue_range"] = None
    return data


def _row_to_profile(row, source: str | None = None) -> CompanyProfile:
    socials = row["socials"]
    if isinstance(socials, str):
        socials = json.loads(socials or "{}")
    cdn_providers = row["cdn_providers"]
    if isinstance(cdn_providers, str):
        cdn_providers = json.loads(cdn_providers or "[]")
    san_domains = row["ssl_san_domains"]
    if isinstance(san_domains, str):
        san_domains = json.loads(san_domains or "[]")

    return CompanyProfile(
        id=row["id"],
        domain=row["domain"],
        name=row["name"],
        description=row["description"],
        industry=row["industry"],
        naics_code=row["naics_code"],
        sic_code=row["sic_code"],
        employee_range=row["employee_range"],
        revenue_range=row["revenue_range"],
        logo_url=row["logo_url"],
        location=row["location"],
        socials=socials or {},
        source=source or row["source"],
        fetched_at=row["fetched_at"],
        web_hosting_provider=row["web_hosting_provider"],
        email_hosting_provider=row["email_hosting_provider"],
        cdn_providers=cdn_providers or [],
        ssl_issuer=row["ssl_issuer"],
        ssl_valid_from=row["ssl_valid_from"],
        ssl_valid_to=row["ssl_valid_to"],
        ssl_days_until_expiry=row["ssl_days_until_expiry"],
        ssl_protocol=row["ssl_protocol"],
        ssl_san_domains=san_domains or [],
    )


async def enrich_company(
    pool: Pool,
    *,
    domain: str | None = None,
    email: str | None = None,
    name: str | None = None,
    user_id: UUID | None = None,
    org_id: UUID | None = None,
    force_refresh: bool = False,
) -> CompanyProfile:
    """Resolve + enrich a company, serving from cache when fresh enough."""
    resolved = await _resolve_domain(domain=domain, email=email, name=name)

    if not force_refresh:
        cached = await get_by_domain(pool, resolved, user_id)
        if cached:
            return _row_to_profile(cached, source="cache")

    if not _domain_is_safe(resolved):
        raise EnrichmentError(f"Domain does not resolve to a public address: {resolved}")

    base_url = f"https://{resolved}"

    # DNS + SSL are independent of the page fetch (and of each other) — a
    # DNS/TLS check is ~seconds; run everything concurrently rather than
    # serially stacking latency.
    home_html, dns_intel, ssl_intel = await asyncio.gather(
        _fetch_page(base_url),
        lookup_domain_intel(resolved),
        inspect_certificate(resolved),
    )
    if not home_html:
        raise EnrichmentError(f"Could not fetch {base_url}")

    meta = _meta_from_html(home_html, base_url)

    from src.scraping.parser.markdown import html_to_markdown

    text = html_to_markdown(home_html, strip_images=True, max_chars=20000)
    about_html = await _fetch_page(urljoin(base_url, "/about"))
    if about_html:
        text += "\n\n" + html_to_markdown(about_html, strip_images=True, max_chars=10000)

    llm = await _llm_firmographics(text, resolved, org_id)
    naics, sic = industry_to_codes(llm.get("industry"))

    row = await upsert_company_profile(
        pool,
        domain=resolved,
        name=llm.get("company_name") or meta.get("name"),
        description=llm.get("description") or meta.get("description"),
        industry=llm.get("industry"),
        naics_code=naics,
        sic_code=sic,
        employee_range=llm.get("employee_range"),
        revenue_range=llm.get("revenue_range"),
        logo_url=meta.get("logo_url"),
        location=llm.get("location"),
        socials=meta.get("socials") or {},
        source="scrape+llm" if llm else "scrape",
        raw={"llm": llm, "meta_name": meta.get("name")},
        org_id=org_id,
        user_id=user_id,
        web_hosting_provider=dns_intel.web_hosting_provider,
        email_hosting_provider=dns_intel.email_hosting_provider,
        cdn_providers=dns_intel.cdn_providers,
        ssl_issuer=ssl_intel.issuer,
        ssl_valid_from=ssl_intel.valid_from,
        ssl_valid_to=ssl_intel.valid_to,
        ssl_days_until_expiry=ssl_intel.days_until_expiry,
        ssl_protocol=ssl_intel.protocol,
        ssl_san_domains=ssl_intel.san_domains,
    )
    return _row_to_profile(row)
