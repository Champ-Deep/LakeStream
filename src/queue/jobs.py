import asyncio
import time
from datetime import datetime
from uuid import UUID

import structlog

from src.db.queries import jobs as job_queries
from src.models.job import JobStatus
from src.models.scraped_data import DataType

# Emit a heartbeat every N seconds of active processing so the stale-job
# cron (10-minute threshold) never kills a legitimately busy job.
_HEARTBEAT_INTERVAL_S = 90

# Hard timeout for the entire scrape job (90 minutes).
# The arq worker has a 2-hour timeout; this fires first so we can
# mark the job as FAILED with a clear message instead of an opaque arq kill.
JOB_HARD_TIMEOUT_SECONDS = 5400

log = structlog.get_logger()


async def process_scrape_job(
    ctx: dict,
    *,
    job_id: str,
    domain: str,
    template_id: str,
    max_pages: int,
    data_types: list[str],
    tier: str | None = None,
    raw_only: bool = False,
    region: str | None = None,
    llm_mode: str = "off",
) -> dict:
    """Main scrape job processor. Orchestrates all workers for a domain.

    Args:
        tier: Optional tier override (e.g., "playwright", "playwright_proxy").
              If provided, bypasses automatic escalation and uses this tier for all fetches.
        raw_only: If True, save only raw page content and skip specialized extraction.
    """
    pool = ctx["pool"]
    start_time = time.time()
    uid = UUID(job_id)

    log.info("job_started", job_id=job_id, domain=domain, data_types=data_types, tier_override=tier)

    # 1. Update job status to running
    await job_queries.update_job_status(pool, uid, JobStatus.RUNNING)

    # Read org_id and user_id from job record for multi-tenancy
    job_record = await job_queries.get_job(pool, uid)
    org_id = str(job_record.org_id) if job_record and job_record.org_id else None
    user_id = str(job_record.user_id) if job_record and job_record.user_id else None

    # Look up org-level proxy URL (configured via settings UI)
    proxy_url = ""
    if org_id:
        proxy_row = await pool.fetchval(
            "SELECT proxy_url FROM organizations WHERE id = $1", job_record.org_id
        )
        proxy_url = proxy_row or ""

    # Background heartbeat task: bumps last_activity_at every _HEARTBEAT_INTERVAL_S
    # so the stale-job cron never falsely kills a legitimately long-running job.
    heartbeat_stop = asyncio.Event()

    async def _heartbeat_loop() -> None:
        while not heartbeat_stop.is_set():
            try:
                await job_queries.update_heartbeat(pool, uid)
            except Exception:
                pass  # Best-effort; don't let heartbeat failure kill the job
            try:
                await asyncio.wait_for(
                    asyncio.shield(heartbeat_stop.wait()), timeout=_HEARTBEAT_INTERVAL_S
                )
            except asyncio.TimeoutError:
                pass  # Normal — keep looping

    heartbeat_task = asyncio.create_task(_heartbeat_loop())

    try:
        # Hard timeout: prevent jobs from hanging forever
        async with asyncio.timeout(JOB_HARD_TIMEOUT_SECONDS):
            # 2. Domain mapping — discover and classify URLs
            from src.workers.domain_mapper import DomainMapperWorker

            # Common kwargs for BaseWorker subclasses
            worker_kwargs = dict(
                domain=domain, job_id=job_id, pool=pool,
                org_id=org_id, user_id=user_id, tier_override=tier,
                proxy_url=proxy_url, region=region,
            )

            # DomainMapperWorker only accepts subset of parameters (not a BaseWorker)
            mapper = DomainMapperWorker(
                domain=domain,
                job_id=job_id,
                org_id=org_id,
                pool=pool,
            )
            classified_urls = await mapper.execute(max_pages=max_pages)

            # Heartbeat after domain mapping
            await job_queries.update_heartbeat(pool, uid)

            # Check for cancellation before starting content extraction
            if await job_queries.is_job_cancelled(pool, uid):
                log.info("job_cancelled_before_extraction", job_id=job_id, domain=domain)
                return {"job_id": job_id, "domain": domain, "data_extracted": 0, "cancelled": True}

            total_data = 0
            errors: list[str] = []

            # 3. Unified ContentWorker: fetch each URL once, extract all data types
            from src.workers.content_worker import ContentWorker

            try:
                # Ensure homepage is in URL list for tech_stack detection
                if "tech_stack" in data_types:
                    homepage = f"https://{domain}"
                    has_homepage = any(
                        c["url"].rstrip("/") == homepage.rstrip("/")
                        for c in classified_urls
                    )
                    if not has_homepage:
                        classified_urls.insert(
                            0, {"url": homepage, "data_type": "page", "confidence": 1.0},
                        )

                content_worker = ContentWorker(**worker_kwargs, raw_only=raw_only, llm_mode=llm_mode)
                results = await content_worker.execute(classified_urls, data_types)
                total_data = len(results)
            except Exception as e:
                log.error("content_worker_error", error=str(e))
                errors.append(f"content_worker: {str(e)}")

            # 4. Mark job complete or failed based on data extracted
            duration_ms = int((time.time() - start_time) * 1000)
            from src.db.queries.domains import get_domain_metadata

            domain_meta = await get_domain_metadata(pool, domain)
            strategy = domain_meta.last_successful_strategy if domain_meta else None

            # Check if any data was extracted before marking as completed
            if total_data == 0:
                # No data extracted - mark as FAILED
                if len(errors) > 0:
                    error_msg = f"No data extracted. Errors: {'; '.join(errors)}"
                    await job_queries.update_job_status(
                        pool,
                        uid,
                        JobStatus.FAILED,
                        error_message=error_msg,
                        duration_ms=duration_ms,
                        pages_scraped=0,
                        completed_at=datetime.now(),
                    )
                    log.warning("job_failed_no_data", job_id=job_id, domain=domain, errors=errors)
                else:
                    error_msg = "No data extracted from domain (empty site or blocked)"
                    await job_queries.update_job_status(
                        pool,
                        uid,
                        JobStatus.FAILED,
                        error_message=error_msg,
                        duration_ms=duration_ms,
                        pages_scraped=0,
                        completed_at=datetime.now(),
                    )
                    log.warning("job_failed_empty_site", job_id=job_id, domain=domain)
            else:
                # Data extracted successfully - mark as COMPLETED
                await job_queries.update_job_status(
                    pool,
                    uid,
                    JobStatus.COMPLETED,
                    strategy_used=strategy,
                    duration_ms=duration_ms,
                    pages_scraped=total_data,
                    completed_at=datetime.now(),
                )

                # 5. Check if domain is tracked and has webhook configured
                from src.db.queries.tracked_domains import get_tracked_domain
                from src.services.webhook_export import export_job_to_webhook

                tracked = await get_tracked_domain(pool, domain)
                if tracked and tracked.webhook_url:
                    try:
                        success = await export_job_to_webhook(uid, tracked.webhook_url)
                        log.info(
                            "webhook_export_completed",
                            job_id=job_id,
                            domain=domain,
                            webhook_url=tracked.webhook_url,
                            success=success,
                        )
                    except Exception as e:
                        log.error(
                            "webhook_export_error",
                            job_id=job_id,
                            domain=domain,
                            webhook_url=tracked.webhook_url,
                            error=str(e),
                        )

            log.info(
                "job_completed",
                job_id=job_id,
                domain=domain,
                data_extracted=total_data,
                duration_ms=duration_ms,
            )

            return {
                "job_id": job_id,
                "domain": domain,
                "data_extracted": total_data,
                "duration_ms": duration_ms,
                "errors": errors,
            }

    except TimeoutError:
        duration_ms = int((time.time() - start_time) * 1000)
        error_msg = f"Job timed out after {JOB_HARD_TIMEOUT_SECONDS // 60} minutes"
        await job_queries.update_job_status(
            pool,
            uid,
            JobStatus.FAILED,
            error_message=error_msg,
            duration_ms=duration_ms,
            completed_at=datetime.now(),
        )
        log.error("job_hard_timeout", job_id=job_id, domain=domain, duration_ms=duration_ms)
        return {
            "job_id": job_id,
            "domain": domain,
            "data_extracted": 0,
            "duration_ms": duration_ms,
            "errors": [error_msg],
        }

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        await job_queries.update_job_status(
            pool,
            uid,
            JobStatus.FAILED,
            error_message=str(e),
            duration_ms=duration_ms,
            completed_at=datetime.now(),
        )
        log.error("job_failed", job_id=job_id, domain=domain, error=str(e))
        raise

    finally:
        # Always stop the heartbeat task when the job finishes (success or failure)
        heartbeat_stop.set()
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except (asyncio.CancelledError, Exception):
            pass


async def process_linkedin_scrape_job(
    ctx: dict,
    *,
    job_id: str,
    search_url: str,
    max_pages: int = 5,
    session_cookies: list[dict] | None = None,
) -> dict:
    """LinkedIn Sales Navigator scrape job processor."""
    pool = ctx["pool"]
    start_time = time.time()
    uid = UUID(job_id)

    log.info("linkedin_job_started", job_id=job_id, search_url=search_url, max_pages=max_pages)
    await job_queries.update_job_status(pool, uid, JobStatus.RUNNING)

    job_record = await job_queries.get_job(pool, uid)
    org_id = str(job_record.org_id) if job_record and job_record.org_id else None
    user_id = str(job_record.user_id) if job_record and job_record.user_id else None

    try:
        from src.services.linkedin_scraper import LinkedInScraper

        await job_queries.update_heartbeat(pool, uid)
        scraper = LinkedInScraper()
        contacts = await scraper.scrape_search_results(
            search_url, max_pages=max_pages, cookies=session_cookies,
        )

        # Save contacts to scraped_data
        total_data = 0
        if contacts:
            from src.db.queries.scraped_data import batch_insert_scraped_data

            records = []
            for c in contacts:
                name = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
                records.append({
                    "job_id": uid,
                    "domain": "linkedin.com",
                    "data_type": DataType.CONTACT,
                    "url": c.get("linkedin_url"),
                    "title": name or c.get("name"),
                    "metadata": c,
                    **({"org_id": UUID(org_id)} if org_id else {}),
                    **({"user_id": UUID(user_id)} if user_id else {}),
                })

            total_data = await batch_insert_scraped_data(pool, records)

        duration_ms = int((time.time() - start_time) * 1000)

        if total_data > 0:
            await job_queries.update_job_status(
                pool, uid, JobStatus.COMPLETED,
                strategy_used="linkedin_server",
                duration_ms=duration_ms,
                pages_scraped=total_data,
                completed_at=datetime.now(),
            )
        else:
            await job_queries.update_job_status(
                pool, uid, JobStatus.FAILED,
                error_message="No contacts extracted (auth may have expired)",
                duration_ms=duration_ms,
                completed_at=datetime.now(),
            )

        log.info(
            "linkedin_job_completed",
            job_id=job_id, contacts=total_data, duration_ms=duration_ms,
        )
        return {"job_id": job_id, "contacts": total_data, "duration_ms": duration_ms}

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        await job_queries.update_job_status(
            pool, uid, JobStatus.FAILED,
            error_message=str(e), duration_ms=duration_ms,
        )
        log.error("linkedin_job_failed", job_id=job_id, error=str(e))
        raise


async def process_apollo_scrape_job(
    ctx: dict,
    *,
    job_id: str,
    search_url: str,
    max_pages: int = 10,
    session_cookies: list[dict] | None = None,
) -> dict:
    """Apollo.io people search scrape job processor."""
    pool = ctx["pool"]
    start_time = time.time()
    uid = UUID(job_id)

    log.info("apollo_job_started", job_id=job_id, search_url=search_url, max_pages=max_pages)
    await job_queries.update_job_status(pool, uid, JobStatus.RUNNING)

    job_record = await job_queries.get_job(pool, uid)
    org_id = str(job_record.org_id) if job_record and job_record.org_id else None
    user_id = str(job_record.user_id) if job_record and job_record.user_id else None

    try:
        from src.services.apollo_scraper import ApolloScraper

        await job_queries.update_heartbeat(pool, uid)
        scraper = ApolloScraper()
        contacts = await scraper.scrape_people_search(
            search_url, max_pages=max_pages, cookies=session_cookies,
        )

        total_data = 0
        if contacts:
            from src.db.queries.scraped_data import batch_insert_scraped_data

            records = []
            for c in contacts:
                name = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
                records.append({
                    "job_id": uid,
                    "domain": "apollo.io",
                    "data_type": DataType.CONTACT,
                    "url": c.get("linkedin_url") or c.get("profile_url"),
                    "title": name or c.get("name"),
                    "metadata": c,
                    **({"org_id": UUID(org_id)} if org_id else {}),
                    **({"user_id": UUID(user_id)} if user_id else {}),
                })

            total_data = await batch_insert_scraped_data(pool, records)

        duration_ms = int((time.time() - start_time) * 1000)

        if total_data > 0:
            await job_queries.update_job_status(
                pool, uid, JobStatus.COMPLETED,
                strategy_used="apollo_server",
                duration_ms=duration_ms,
                pages_scraped=total_data,
                completed_at=datetime.now(),
            )
        else:
            await job_queries.update_job_status(
                pool, uid, JobStatus.FAILED,
                error_message="No contacts extracted (auth may have expired)",
                duration_ms=duration_ms,
                completed_at=datetime.now(),
            )

        log.info(
            "apollo_job_completed",
            job_id=job_id, contacts=total_data, duration_ms=duration_ms,
        )
        return {"job_id": job_id, "contacts": total_data, "duration_ms": duration_ms}

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        await job_queries.update_job_status(
            pool, uid, JobStatus.FAILED,
            error_message=str(e), duration_ms=duration_ms,
        )
        log.error("apollo_job_failed", job_id=job_id, error=str(e))
        raise
