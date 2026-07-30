"""ATS job-board endpoints (Greenhouse / Lever / Ashby).

Public JSON APIs — no proxy tier, no login, no LLM cost. Useful as both a
buying signal (who is hiring, for what, where) and as first-party prose.

Both endpoints accept `persist: true` to write the postings into scraped_data
as DataType.JOB_POSTING. Without persistence these routes were read-only and
the hiring-spike signal had nothing to read — see
services/job_boards.persist_postings for why that mattered.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from uuid import UUID, uuid4

from src.services.job_boards import (
    ATS_PROVIDERS,
    fetch_board,
    fetch_jobs_for_companies,
    fetch_jobs_for_company,
    persist_board_results,
    persist_postings,
    summarise_hiring,
)


def _persist_ctx(request: Request, body: dict):
    """(pool, org_id, job_id) when persistence is requested and available.

    Returns (None, None, None) when the caller did not ask to persist or the DB
    pool is not on app state — the fetch still succeeds and simply writes
    nothing, so a read-only deployment is unaffected.
    """
    if not body.get("persist"):
        return None, None, None
    pool = getattr(request.app.state, "pool", None) or getattr(
        request.app.state, "db_pool", None
    )
    if pool is None:
        return None, None, None
    raw_org = body.get("org_id")
    org_id = UUID(str(raw_org)) if raw_org else None
    # * Each fetch is its own provenance group. scraped_data upserts on
    # * (domain, url, data_type), so a fresh job_id per call does not duplicate
    # * rows — it records which run last touched them.
    return pool, org_id, uuid4()

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

    persisted = 0
    pool, org_id, job_id = _persist_ctx(request, body)
    if pool is not None:
        persisted = await persist_postings(
            pool, result, domain=domain or result.board_token,
            job_id=job_id, org_id=org_id,
        )

    return {
        "success": True,
        "provider": result.provider,
        "board_token": result.board_token,
        "count": len(result.postings),
        "persisted": persisted,
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

    persisted = 0
    pool, org_id, job_id = _persist_ctx(request, body)
    if pool is not None:
        persisted = await persist_board_results(
            pool, results, job_id=job_id, org_id=org_id
        )

    found = sum(1 for r in payload.values() if r.get("success"))
    return {
        "success": True,
        "companies": len(payload),
        "boards_found": found,
        "persisted": persisted,
        "total_postings": sum(r.get("count", 0) for r in payload.values()),
        "results": payload,
    }
