"""Tech Detect API — synchronous single-URL detection and durable batch runs.

Deliberately not mounted under /api/scrape: that prefix is in BILLABLE_PREFIXES,
which would attach a credit check and a usage write to every poll.
"""

from __future__ import annotations

import uuid
from datetime import date
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.api.middleware.auth import get_current_user
from src.db.pool import get_pool

router = APIRouter(prefix="/tech-detect", tags=["tech-detect"])
log = structlog.get_logger()

TECH_DETECT_NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")
MAX_INLINE_URLS = 100


class DetectRequest(BaseModel):
    url: str = Field(..., min_length=3, max_length=2048)
    wappalyzer: bool = True
    persist: bool = True


class BatchRequest(BaseModel):
    urls: list[str] = Field(default_factory=list)
    wappalyzer: bool = True
    save_to_results: bool = False
    client_token: str | None = None


def _ids(user: dict) -> tuple[UUID, UUID | None]:
    org_id = user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_id = user.get("user_id") or user.get("id")
    return UUID(str(org_id)), UUID(str(user_id)) if user_id else None


async def ensure_rollup_job(pool, org_id: UUID, user_id: UUID | None) -> UUID:
    """One virtual scrape_jobs row per user per day for single detections.

    scraped_data.job_id must be a real UUID (ScrapedData.job_id is non-Optional,
    so a NULL would raise on every /results read). A deterministic daily id keeps
    50 lookups from creating 50 job rows.
    """
    key = f"{user_id or org_id}:{date.today().isoformat()}"
    job_id = uuid.uuid5(TECH_DETECT_NS, key)
    await pool.execute(
        """INSERT INTO scrape_jobs
               (id, domain, template_id, status, strategy_used,
                pages_scraped, org_id, user_id, completed_at)
           VALUES ($1, $2, 'tech_detect', 'completed', 'tech_detect',
                   0, $3, $4, NOW())
           ON CONFLICT (id) DO NOTHING""",
        job_id,
        "tech-detect",
        org_id,
        user_id,
    )
    return job_id


async def persist_results(pool, results: list[dict], *, org_id: UUID,
                          user_id: UUID | None, job_id: UUID) -> int:
    """Write tech_stack rows via batch_insert_scraped_data.

    insert_scraped_data hardcodes user_id=None, which would hide these rows from
    their own author on /results, so the batch helper is the only correct path.
    """
    from src.db.queries.scraped_data import batch_insert_scraped_data
    from src.models.scraped_data import DataType, TechStackMetadata

    records = []
    for r in results:
        if r.get("status") != "ok":
            continue
        fields = r.get("fields", {})
        meta = TechStackMetadata(
            **{k: v for k, v in fields.items() if k in TechStackMetadata.model_fields},
            detections=r.get("detections", []),
        ).model_dump()
        records.append({
            "job_id": job_id,
            "domain": r.get("domain") or "",
            "data_type": DataType.TECH_STACK,
            "url": r.get("final_url"),
            "title": f"Tech Stack: {r.get('domain') or r.get('final_url')}",
            "metadata": meta,
            "org_id": org_id,
            "user_id": user_id,
        })
    if not records:
        return 0
    return await batch_insert_scraped_data(pool, records)


@router.post("/detect")
async def detect_single(body: DetectRequest, user: dict = Depends(get_current_user)):
    """Fetch and detect one URL synchronously.

    Always 200 — network and block conditions come back as `status`, matching
    the /scrape/extract convention.
    """
    from src.services.tech_detect import detect_one

    org_id, user_id = _ids(user)
    result = await detect_one(body.url, wappalyzer=body.wappalyzer)

    saved = 0
    if body.persist and result.get("status") == "ok":
        try:
            pool = await get_pool()
            job_id = await ensure_rollup_job(pool, org_id, user_id)
            saved = await persist_results(pool, [result], org_id=org_id,
                                          user_id=user_id, job_id=job_id)
        except Exception as e:  # noqa: BLE001 - persistence must not fail the answer
            log.warning("tech_detect_persist_failed", url=body.url, error=str(e))

    result["saved"] = bool(saved)
    result["success"] = result.get("status") == "ok"
    return result


@router.post("/parse-csv")
async def parse_csv(request: Request, user: dict = Depends(get_current_user)):
    """Parse an uploaded CSV into a URL list without starting a run."""
    from src.services.bulk_upload import parse_url_csv

    _ids(user)
    form = await request.form()
    csv_file = form.get("csv_file")
    if not csv_file or not hasattr(csv_file, "read"):
        raise HTTPException(status_code=400, detail="No CSV file uploaded")

    content = await csv_file.read()
    parsed = parse_url_csv(content, filename=getattr(csv_file, "filename", "") or "")
    if parsed.error and not parsed.valid:
        raise HTTPException(status_code=400, detail=parsed.error)

    return {
        "urls": [p.domain for p in parsed.valid],
        "invalid": [p.raw for p in parsed.invalid][:20],
        "invalid_count": len(parsed.invalid),
        "duplicates": parsed.duplicates_in_file,
        "warning": parsed.error,
    }


@router.post("/batch", status_code=202)
async def start_batch(body: BatchRequest, user: dict = Depends(get_current_user)):
    """Create a run and hand it to the worker."""
    from arq.connections import RedisSettings
    from arq.connections import create_pool as create_arq_pool

    from src.config.settings import get_settings
    from src.db.queries.tech_detect import create_run

    org_id, user_id = _ids(user)
    urls = [u.strip() for u in body.urls if u and u.strip()]
    if not urls:
        raise HTTPException(status_code=422, detail="No URLs supplied")
    if len(urls) > MAX_INLINE_URLS:
        raise HTTPException(
            status_code=422,
            detail=f"Too many URLs ({len(urls)}). Maximum is {MAX_INLINE_URLS}.",
        )

    pool = await get_pool()
    run_id, created = await create_run(
        pool,
        org_id=org_id,
        user_id=user_id,
        urls=urls,
        source="list",
        options={"wappalyzer": body.wappalyzer, "save_to_results": body.save_to_results},
        client_token=body.client_token,
    )

    if created:
        settings = get_settings()
        redis = await create_arq_pool(RedisSettings.from_dsn(settings.redis_url))
        try:
            await redis.enqueue_job("process_tech_detect_run", run_id=str(run_id))
        finally:
            await redis.aclose()

    return {"run_id": str(run_id), "total": len(urls), "created": created}


def _serialize_run(run: dict, results: list[dict]) -> dict:
    return {
        "run_id": str(run["id"]),
        "status": run["status"],
        "total": run["total"],
        "done": run["done_count"],
        "ok": run["ok_count"],
        "blocked": run["blocked_count"],
        "failed": run["failed_count"],
        "skipped": run["skipped_count"],
        "error": run.get("error_message"),
        "created_at": run["created_at"].isoformat() if run.get("created_at") else None,
        "completed_at": run["completed_at"].isoformat() if run.get("completed_at") else None,
        "results": [
            {
                "position": r["position"],
                "input_url": r["input_url"],
                "final_url": r["final_url"],
                "domain": r["domain"],
                "status": r["status"],
                "http_status": r["http_status"],
                "error": r["error"],
                "duration_ms": r["duration_ms"],
                # `fields` duplicates `by_category` and is unused by the client.
                **{k: v for k, v in (r.get("detect") or {}).items() if k != "fields"},
            }
            for r in results
        ],
    }


@router.get("/runs")
async def recent_runs(limit: int = 10, user: dict = Depends(get_current_user)):
    from src.db.queries.tech_detect import list_runs

    org_id, user_id = _ids(user)
    pool = await get_pool()
    runs = await list_runs(
        pool,
        user_id=None if user.get("is_admin") else user_id,
        org_id=org_id,
        limit=max(1, min(limit, 50)),
    )
    return {
        "runs": [
            {
                "run_id": str(r["id"]),
                "status": r["status"],
                "total": r["total"],
                "done": r["done_count"],
                "ok": r["ok_count"],
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            }
            for r in runs
        ]
    }


@router.get("/runs/{run_id}")
async def run_status(run_id: UUID, user: dict = Depends(get_current_user)):
    from src.db.queries.tech_detect import get_results, get_run

    org_id, user_id = _ids(user)
    pool = await get_pool()
    run = await get_run(pool, run_id)

    # 404 rather than 403 on a mismatch so run ids cannot be probed.
    if not run or run["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Run not found")
    if not user.get("is_admin") and run["user_id"] and run["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Run not found")

    return _serialize_run(run, await get_results(pool, run_id))
