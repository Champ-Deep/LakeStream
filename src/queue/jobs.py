import time
from datetime import datetime
from uuid import UUID

import structlog

from src.db.queries import jobs as job_queries
from src.models.job import JobStatus

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
) -> dict:
    """Main scrape job processor. Orchestrates all workers for a domain.

    Args:
        tier: Optional tier override (e.g., "playwright", "playwright_proxy").
              If provided, bypasses automatic escalation and uses this tier for all fetches.
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

    try:
        # 2. Domain mapping — discover and classify URLs
        from src.workers.domain_mapper import DomainMapperWorker

        # Common kwargs for BaseWorker subclasses
        worker_kwargs = dict(
            domain=domain, job_id=job_id, pool=pool,
            org_id=org_id, user_id=user_id, tier_override=tier, proxy_url=proxy_url,
        )

        # DomainMapperWorker only accepts subset of parameters (not a BaseWorker)
        mapper = DomainMapperWorker(
            domain=domain,
            job_id=job_id,
            org_id=org_id
        )
        classified_urls = await mapper.execute(max_pages=max_pages)

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

            content_worker = ContentWorker(**worker_kwargs)
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
                # Errors occurred during scraping
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
                # No errors but no data - domain might be empty or blocked
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

            # 5. Check if domain is tracked and has webhook configured (only for successful jobs)
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
                    # Don't fail job if webhook export fails
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

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        await job_queries.update_job_status(
            pool,
            uid,
            JobStatus.FAILED,
            error_message=str(e),
            duration_ms=duration_ms,
        )
        log.error("job_failed", job_id=job_id, domain=domain, error=str(e))
        raise
