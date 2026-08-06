"""Technology lookup API (v2.2).

POST /api/tech        — one domain in, full technology profile out
POST /api/tech/bulk   — up to N domains, resolved concurrently

A "full profile" merges two layers:
  - page-level signals from the homepage (CMS, frameworks, JS libraries,
    analytics, widgets, web server, programming languages, OS, CDN), detected
    by the precompiled catalog engine; and
  - domain-level facts (web hosting, email hosting, CDN, SSL certificate)
    resolved from DNS + a TLS handshake, which don't vary per page.

The LLM judge (when enabled) runs last and can only remove clear false
positives from the regex output — it never adds technologies.
"""

import asyncio

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from uuid import UUID

from src.api.middleware.auth import get_current_user
from src.config.settings import get_settings
from src.services.dns_intel import lookup_domain_intel
from src.services.ssl_intel import inspect_certificate

router = APIRouter(prefix="/tech")
logger = structlog.get_logger()

MAX_BULK_DOMAINS = 50
_BULK_CONCURRENCY = 10


class TechRequest(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    judge: bool | None = None       # override enable_tech_judge per request
    include_detections: bool = True  # per-detection audit trail


class BulkTechRequest(BaseModel):
    domains: list[str] = Field(min_length=1, max_length=MAX_BULK_DOMAINS)
    judge: bool | None = None
    include_detections: bool = False  # off by default — keeps bulk responses small


def _normalize(domain: str) -> str:
    import re

    d = domain.strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0].split(":")[0]
    return d.removeprefix("www.")


async def _fetch_homepage(domain: str) -> tuple[str, dict[str, str], str]:
    """Return (html, headers, final_url) for a domain's homepage."""
    from src.models.scraping import FetchOptions, ScrapingTier
    from src.scraping.fetcher.factory import create_fetcher

    settings = get_settings()
    tier = (
        ScrapingTier.GO_HTTP
        if settings.enable_go_fetchers and settings.go_http_fetcher_url
        else ScrapingTier.PLAYWRIGHT
    )
    url = f"https://{domain}"
    try:
        fetcher = create_fetcher(tier)
        result = await fetcher.fetch(url, FetchOptions(tier=tier))
        if result.html and not result.blocked:
            return result.html, (result.headers or {}), (result.url or url)
        logger.debug("tech_fetch_empty", domain=domain, blocked=result.blocked)
    except Exception as e:
        logger.debug("tech_fetch_failed", domain=domain, error=str(e))
    return "", {}, url


async def analyze_domain(
    domain: str,
    *,
    judge: bool | None = None,
    include_detections: bool = True,
    org_id: UUID | None = None,
) -> dict:
    """Full technology profile for one domain."""
    from src.scraping.parser.tech_engine import (
        detect,
        detections_to_metadata,
        extract_page_signals,
    )

    normalized = _normalize(domain)
    if not normalized or "." not in normalized:
        return {"domain": domain, "success": False, "error": "Invalid domain"}

    # Page fetch, DNS, and TLS are independent — run them together.
    (html, headers, final_url), dns, ssl = await asyncio.gather(
        _fetch_homepage(normalized),
        lookup_domain_intel(normalized),
        inspect_certificate(normalized),
    )

    dns_records = {
        "ns": " ".join(dns.nameservers),
        "mx": " ".join(dns.mx_hosts),
        "a": " ".join(dns.a_records),
    }
    signals = extract_page_signals(
        html,
        url=final_url,
        headers=headers,
        dns=dns_records,
        cert_issuer=ssl.issuer or "",
    )
    detections = detect(signals)
    profile = detections_to_metadata(detections)

    rejected: list[dict] = []
    use_judge = get_settings().enable_tech_judge if judge is None else judge
    if use_judge and profile.get("detections"):
        from src.services.tech_judge import judge_detections

        _kept, rejected = await judge_detections(
            profile["detections"], url=final_url, org_id=org_id,
            force=(judge is True),
        )
        if rejected:
            rejected_names = {r["name"].strip().lower() for r in rejected}
            profile = detections_to_metadata([
                d for d in detections
                if d.name.strip().lower() not in rejected_names
            ])

    result = {
        "domain": normalized,
        "success": bool(html) or bool(dns.nameservers),
        "url": final_url,
        "fetched": bool(html),
        # page-level
        **{k: v for k, v in profile.items() if k != "detections"},
        # domain-level (DNS + TLS)
        "web_hosting_provider": dns.web_hosting_provider,
        "email_hosting_provider": dns.email_hosting_provider,
        "cdn_providers": dns.cdn_providers,
        "nameservers": dns.nameservers,
        "mx_hosts": dns.mx_hosts,
        "ssl": {
            "issuer": ssl.issuer,
            "subject_cn": ssl.subject_cn,
            "protocol": ssl.protocol,
            "valid_from": ssl.valid_from.isoformat() if ssl.valid_from else None,
            "valid_to": ssl.valid_to.isoformat() if ssl.valid_to else None,
            "days_until_expiry": ssl.days_until_expiry,
            "san_domains": ssl.san_domains[:25],
        },
        "technology_count": len(profile.get("detections") or []),
    }
    if include_detections:
        result["detections"] = profile.get("detections") or []
    if rejected:
        result["rejected_by_judge"] = rejected
    if not html:
        result["warning"] = "Homepage could not be fetched — DNS/SSL only"
    return result


@router.post("")
async def tech_lookup(
    input: TechRequest, user: dict = Depends(get_current_user)
) -> dict:
    """Full technology profile for a single domain."""
    org_id = UUID(user["org_id"]) if user.get("org_id") else None
    try:
        return await analyze_domain(
            input.domain,
            judge=input.judge,
            include_detections=input.include_detections,
            org_id=org_id,
        )
    except Exception as e:
        logger.error("tech_lookup_failed", domain=input.domain, error=str(e))
        raise HTTPException(status_code=500, detail=f"Lookup failed: {e}")


@router.post("/bulk")
async def tech_lookup_bulk(
    input: BulkTechRequest, user: dict = Depends(get_current_user)
) -> dict:
    """Technology profiles for many domains, resolved concurrently."""
    org_id = UUID(user["org_id"]) if user.get("org_id") else None
    sem = asyncio.Semaphore(_BULK_CONCURRENCY)

    async def one(d: str) -> dict:
        async with sem:
            try:
                return await analyze_domain(
                    d,
                    judge=input.judge,
                    include_detections=input.include_detections,
                    org_id=org_id,
                )
            except Exception as e:
                logger.warning("tech_bulk_item_failed", domain=d, error=str(e))
                return {"domain": d, "success": False, "error": str(e)[:200]}

    results = await asyncio.gather(*(one(d) for d in input.domains))
    return {
        "success": True,
        "count": len(results),
        "succeeded": sum(1 for r in results if r.get("success")),
        "results": results,
    }


@router.get("/catalog")
async def catalog_info(user: dict = Depends(get_current_user)) -> dict:
    """Report what fingerprint catalog is loaded (size + source)."""
    from src.scraping.parser.tech_engine import get_catalog

    settings = get_settings()
    catalog = get_catalog()
    by_target: dict[str, int] = {}
    for sig in catalog.signatures:
        by_target[sig.target] = by_target.get(sig.target, 0) + 1
    return {
        "success": True,
        "signatures": catalog.size,
        "technologies": len(catalog.meta) or catalog.size,
        "external_catalog_path": settings.tech_catalog_path or None,
        "signatures_by_target": by_target,
        "judge_enabled": settings.enable_tech_judge,
    }
