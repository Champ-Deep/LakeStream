"""ATS job-board endpoints (Greenhouse / Lever / Ashby).

Public JSON APIs — no proxy tier, no login, no LLM cost. Useful as both a
buying signal (who is hiring, for what, where) and as first-party prose.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from src.services.job_boards import (
    ATS_PROVIDERS,
    fetch_board,
    fetch_jobs_for_companies,
    fetch_jobs_for_company,
    summarise_hiring,
)

router = APIRouter(prefix="/jobs")

# Bound the batch endpoint — these are third-party servers.
_MAX_BATCH = 50


@router.post("/board")
async def get_board(request: Request):
    """Fetch one company's open roles.

    Body: {domain: str} to auto-discover the board, or
          {provider: "greenhouse"|"lever"|"ashby", token: str} to skip discovery.
    """
    body = await request.json()
    provider = (body.get("provider") or "").strip().lower()
    token = (body.get("token") or "").strip()
    domain = (body.get("domain") or "").strip()

    if provider and token:
        result = await fetch_board(provider, token)
    elif domain:
        result = await fetch_jobs_for_company(domain)
    else:
        return {
            "success": False,
            "error": "Provide either {domain} or {provider, token}",
            "valid_providers": list(ATS_PROVIDERS),
        }

    if not result.ok:
        return {
            "success": False,
            "error": result.error,
            "provider": result.provider,
            "board_token": result.board_token,
        }

    return {
        "success": True,
        "provider": result.provider,
        "board_token": result.board_token,
        "count": len(result.postings),
        "signals": summarise_hiring(result.postings),
        "postings": [p.to_dict() for p in result.postings],
    }


@router.post("/boards/batch")
async def get_boards_batch(request: Request):
    """Fetch open roles for many companies at once.

    Body: {domains: [str], concurrency?: int}
    Returns per-domain results; a failure on one domain never fails the batch.
    """
    body = await request.json()
    domains = body.get("domains") or []
    if not isinstance(domains, list) or not domains:
        return {"success": False, "error": "domains must be a non-empty array"}
    if len(domains) > _MAX_BATCH:
        return {
            "success": False,
            "error": f"Too many domains ({len(domains)}); max {_MAX_BATCH} per call",
        }

    concurrency = min(max(int(body.get("concurrency", 5) or 5), 1), 10)
    results = await fetch_jobs_for_companies(domains, concurrency=concurrency)

    payload = {}
    for domain, result in results.items():
        if result.ok:
            payload[domain] = {
                "success": True,
                "provider": result.provider,
                "board_token": result.board_token,
                "count": len(result.postings),
                "signals": summarise_hiring(result.postings),
                "postings": [p.to_dict() for p in result.postings],
            }
        else:
            payload[domain] = {"success": False, "error": result.error}

    found = sum(1 for r in payload.values() if r.get("success"))
    return {
        "success": True,
        "companies": len(payload),
        "boards_found": found,
        "total_postings": sum(r.get("count", 0) for r in payload.values()),
        "results": payload,
    }
