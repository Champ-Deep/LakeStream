"""arq job that processes a Tech Detect batch run.

Runs in the worker rather than the API because detection is ~2.5s of GIL-held
CPU per URL; a 100-URL batch would otherwise stall every other request.
"""

import asyncio
from uuid import UUID

import structlog

log = structlog.get_logger()

FETCH_CONCURRENCY = 10
POOL_WORKERS = 4
MAX_ESCALATIONS = 10


async def process_tech_detect_run(ctx: dict, *, run_id: str) -> dict:
    """Process every pending URL in a run, writing each result as it lands."""
    from src.db.queries.tech_detect import (
        finish_run,
        get_pending_urls,
        get_run,
        mark_running,
        save_result,
    )
    from src.services.tech_detect import detect_one

    pool = ctx["pool"]
    rid = UUID(run_id)

    run = await get_run(pool, rid)
    if not run:
        log.warning("tech_detect_run_missing", run_id=run_id)
        return {"run_id": run_id, "status": "missing"}
    if run["status"] in ("completed", "cancelled"):
        return {"run_id": run_id, "status": run["status"]}

    options = run.get("options") or {}
    if isinstance(options, str):
        import json

        options = json.loads(options)
    wappalyzer = bool(options.get("wappalyzer", True))
    save_to_results = bool(options.get("save_to_results", False))

    await mark_running(pool, rid)
    pending = await get_pending_urls(pool, rid)
    log.info("tech_detect_run_started", run_id=run_id, pending=len(pending),
             wappalyzer=wappalyzer, save_to_results=save_to_results)

    sem = asyncio.Semaphore(FETCH_CONCURRENCY)
    escalations = {"used": 0}
    completed: list[dict] = []
    lock = asyncio.Lock()

    async def handle(position: int, url: str) -> None:
        async with sem:
            async with lock:
                allow_escalation = escalations["used"] < MAX_ESCALATIONS
            try:
                result = await detect_one(
                    url,
                    wappalyzer=wappalyzer,
                    allow_escalation=allow_escalation,
                    max_workers=POOL_WORKERS,
                )
            except Exception as e:  # noqa: BLE001 - one bad URL must not kill the run
                log.warning("tech_detect_url_failed", run_id=run_id, url=url, error=str(e))
                result = {
                    "input_url": url, "final_url": url, "domain": "",
                    "status": "failed", "http_status": None, "error": repr(e),
                    "detections": [], "by_category": {}, "fields": {},
                    "total": 0, "recommended": 0, "duration_ms": 0,
                }
            if result.get("escalated"):
                async with lock:
                    escalations["used"] += 1
            try:
                await save_result(pool, rid, position, result)
            except Exception as e:  # noqa: BLE001
                log.error("tech_detect_save_failed", run_id=run_id, url=url, error=str(e))
            if result.get("status") == "ok":
                async with lock:
                    completed.append(result)

    try:
        await asyncio.gather(*(handle(p, u) for p, u in pending), return_exceptions=True)

        if save_to_results and completed:
            await _persist_batch(pool, rid, run, completed)

        await finish_run(pool, rid)
        log.info("tech_detect_run_completed", run_id=run_id, ok=len(completed))
        return {"run_id": run_id, "status": "completed", "ok": len(completed)}
    except Exception as e:  # noqa: BLE001
        log.error("tech_detect_run_failed", run_id=run_id, error=str(e))
        await finish_run(pool, rid, error=str(e))
        return {"run_id": run_id, "status": "failed", "error": str(e)}


async def _persist_batch(pool, rid: UUID, run: dict, results: list[dict]) -> None:
    """Opt-in write of batch results into scraped_data, behind one virtual job."""
    from uuid import uuid4

    from src.api.routes.tech_detect import persist_results

    try:
        job_id = uuid4()
        await pool.execute(
            """INSERT INTO scrape_jobs
                   (id, domain, template_id, status, strategy_used,
                    pages_scraped, org_id, user_id, completed_at)
               VALUES ($1, $2, 'tech_detect', 'completed', 'tech_detect',
                       $3, $4, $5, NOW())""",
            job_id,
            f"tech-detect-run-{str(rid)[:8]}",
            len(results),
            run["org_id"],
            run["user_id"],
        )
        count = await persist_results(
            pool, results, org_id=run["org_id"], user_id=run["user_id"], job_id=job_id
        )
        log.info("tech_detect_batch_persisted", run_id=str(rid), records=count)
    except Exception as e:  # noqa: BLE001 - saving is a bonus, never fail the run
        log.warning("tech_detect_batch_persist_failed", run_id=str(rid), error=str(e))


async def recover_tech_detect_runs_cron(ctx: dict) -> None:
    """Cron: fail runs whose worker died so they stop showing as running."""
    from src.db.queries.tech_detect import recover_orphan_runs

    count = await recover_orphan_runs(ctx["pool"])
    if count:
        log.info("tech_detect_runs_recovered", count=count)
