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

        # 3. For each requested data_type, run the appropriate worker
        from src.workers.article_parser import ArticleParserWorker
        from src.workers.blog_extractor import BlogExtractorWorker
        from src.workers.contact_finder import ContactFinderWorker
        from src.workers.resource_finder import ResourceFinderWorker
        from src.workers.tech_detector import TechDetectorWorker

        blog_urls: list[str] = []

        for dtype in data_types:
            try:
                if dtype == "blog_url":
                    worker = BlogExtractorWorker(**worker_kwargs)
                    result = await worker.execute(
                        [u["url"] for u in classified_urls if u.get("data_type") == "blog_url"]
                    )
                    # Extract individual article URLs discovered by BlogExtractor
                    blog_urls = []
                    for r in result:
                        if r.metadata and isinstance(r.metadata, dict):
                            article_urls = r.metadata.get("article_urls", [])
                            if article_urls:
                                blog_urls.extend(article_urls)
                            elif r.url:
                                # No article links found — page is likely an article itself
                                blog_urls.append(r.url)
                    total_data += len(result)

                elif dtype == "article":
                    worker = ArticleParserWorker(**worker_kwargs)  # type: ignore[assignment]
                    result = await worker.execute(blog_urls)
                    total_data += len(result)

                elif dtype == "contact":
                    worker = ContactFinderWorker(**worker_kwargs)  # type: ignore[assignment]
                    result = await worker.execute(
                        [u["url"] for u in classified_urls if u.get("data_type") == "contact"]
                    )
                    total_data += len(result)

                elif dtype == "tech_stack":
                    worker = TechDetectorWorker(**worker_kwargs)  # type: ignore[assignment]
                    result = await worker.execute([f"https://{domain}"])
                    total_data += len(result)

                elif dtype == "resource":
                    worker = ResourceFinderWorker(**worker_kwargs)  # type: ignore[assignment]
                    result = await worker.execute(
                        [u["url"] for u in classified_urls if u.get("data_type") == "resource"]
                    )
                    total_data += len(result)

                elif dtype == "pricing":
                    from src.workers.pricing_finder import PricingFinderWorker

                    worker = PricingFinderWorker(**worker_kwargs)  # type: ignore[assignment]
                    result = await worker.execute(
                        [u["url"] for u in classified_urls if u.get("data_type") == "pricing"]
                    )
                    total_data += len(result)

            except Exception as e:
                log.error("worker_error", dtype=dtype, error=str(e))
                errors.append(f"{dtype}: {str(e)}")

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
