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
) -> dict:
    """Main scrape job processor. Orchestrates all workers for a domain."""
    pool = ctx["pool"]
    start_time = time.time()
    uid = UUID(job_id)

    log.info("job_started", job_id=job_id, domain=domain, data_types=data_types)

    # 1. Update job status to running
    await job_queries.update_job_status(pool, uid, JobStatus.RUNNING)

    try:
        # 2. Domain mapping — discover and classify URLs
        from src.workers.domain_mapper import DomainMapperWorker

        mapper = DomainMapperWorker(domain=domain, job_id=job_id)
        classified_urls = await mapper.execute(max_pages=max_pages)

        total_data = len(classified_urls)
        total_cost = 0.0
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
                    worker = BlogExtractorWorker(domain=domain, job_id=job_id)
                    result = await worker.execute(
                        [u["url"] for u in classified_urls if u.get("data_type") == "blog_url"]
                    )
                    blog_urls = [r.url for r in result if r.url]
                    total_data += len(result)

                elif dtype == "article":
                    worker = ArticleParserWorker(domain=domain, job_id=job_id)  # type: ignore[assignment]
                    result = await worker.execute(blog_urls)
                    total_data += len(result)

                elif dtype == "contact":
                    worker = ContactFinderWorker(domain=domain, job_id=job_id)  # type: ignore[assignment]
                    result = await worker.execute(
                        [u["url"] for u in classified_urls if u.get("data_type") == "contact"]
                    )
                    total_data += len(result)

                elif dtype == "tech_stack":
                    worker = TechDetectorWorker(domain=domain, job_id=job_id)  # type: ignore[assignment]
                    result = await worker.execute([f"https://{domain}"])
                    total_data += len(result)

                elif dtype == "resource":
                    worker = ResourceFinderWorker(domain=domain, job_id=job_id)  # type: ignore[assignment]
                    result = await worker.execute(
                        [u["url"] for u in classified_urls if u.get("data_type") == "resource"]
                    )
                    total_data += len(result)

            except Exception as e:
                log.error("worker_error", dtype=dtype, error=str(e))
                errors.append(f"{dtype}: {str(e)}")

        # 4. Mark job complete
        duration_ms = int((time.time() - start_time) * 1000)
        await job_queries.update_job_status(
            pool,
            uid,
            JobStatus.COMPLETED,
            duration_ms=duration_ms,
            pages_scraped=total_data,
            cost_usd=total_cost,
            completed_at=datetime.now(),
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
