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

    try:
        # 2. Domain mapping — discover and classify URLs
        from src.workers.domain_mapper import DomainMapperWorker

        mapper = DomainMapperWorker(domain=domain, job_id=job_id, org_id=org_id, tier_override=tier)
        classified_urls = await mapper.execute(max_pages=max_pages)

        total_data = 0
        errors: list[str] = []
        # Observability: track URLs discovered vs actually extracted per data type
        urls_discovered: dict[str, int] = {}
        urls_extracted: dict[str, int] = {}

        # 3. For each requested data_type, run the appropriate worker
        from src.workers.article_parser import ArticleParserWorker
        from src.workers.blog_extractor import BlogExtractorWorker
        from src.workers.contact_finder import ContactFinderWorker
        from src.workers.resource_finder import ResourceFinderWorker
        from src.workers.tech_detector import TechDetectorWorker

        # Collect article URLs from two sources:
        # 1. BlogExtractorWorker (follows pagination on blog index pages)
        # 2. URLs the sitemap/crawler already classified directly as ARTICLE
        #    (individual article slugs discovered at map time — no re-fetching needed)
        blog_urls: list[str] = []

        # Seed blog_urls with any individual article URLs already found during domain mapping
        sitemap_article_urls = [u["url"] for u in classified_urls if u.get("data_type") == "article"]
        if sitemap_article_urls:
            blog_urls.extend(sitemap_article_urls)
            log.info(
                "sitemap_articles_seeded",
                job_id=job_id,
                domain=domain,
                count=len(sitemap_article_urls),
            )

        for dtype in data_types:
            try:
                if dtype == "blog_url":
                    candidate_urls = [u["url"] for u in classified_urls if u.get("data_type") == "blog_url"]
                    urls_discovered["blog_url"] = len(candidate_urls)
                    worker = BlogExtractorWorker(
                        domain=domain, job_id=job_id, pool=pool, org_id=org_id, user_id=user_id, tier_override=tier
                    )
                    result = await worker.execute(candidate_urls)
                    # Collect article URLs discovered by BlogExtractor (via pagination)
                    extractor_article_urls: list[str] = []
                    for r in result:
                        if r.metadata and isinstance(r.metadata, dict):
                            article_urls = r.metadata.get("article_urls", [])
                            if article_urls:
                                extractor_article_urls.extend(article_urls)
                            elif r.url:
                                extractor_article_urls.append(r.url)

                    # Merge into blog_urls, deduplicating against sitemap articles already added
                    existing = set(blog_urls)
                    for u in extractor_article_urls:
                        if u not in existing:
                            blog_urls.append(u)
                            existing.add(u)

                    urls_extracted["blog_url"] = len(result)
                    total_data += len(result)

                elif dtype == "article":
                    urls_discovered["article"] = len(blog_urls)
                    worker = ArticleParserWorker(domain=domain, job_id=job_id, pool=pool, org_id=org_id, user_id=user_id, tier_override=tier)  # type: ignore[assignment]
                    result = await worker.execute(blog_urls)
                    urls_extracted["article"] = len(result)
                    total_data += len(result)

                elif dtype == "contact":
                    candidate_urls = [u["url"] for u in classified_urls if u.get("data_type") == "contact"]
                    urls_discovered["contact"] = len(candidate_urls)
                    worker = ContactFinderWorker(domain=domain, job_id=job_id, pool=pool, org_id=org_id, user_id=user_id, tier_override=tier)  # type: ignore[assignment]
                    result = await worker.execute(candidate_urls)
                    urls_extracted["contact"] = len(result)
                    total_data += len(result)

                elif dtype == "tech_stack":
                    urls_discovered["tech_stack"] = 1
                    worker = TechDetectorWorker(domain=domain, job_id=job_id, pool=pool, org_id=org_id, user_id=user_id, tier_override=tier)  # type: ignore[assignment]
                    result = await worker.execute([f"https://{domain}"])
                    urls_extracted["tech_stack"] = len(result)
                    total_data += len(result)

                elif dtype == "resource":
                    candidate_urls = [u["url"] for u in classified_urls if u.get("data_type") == "resource"]
                    urls_discovered["resource"] = len(candidate_urls)
                    worker = ResourceFinderWorker(domain=domain, job_id=job_id, pool=pool, org_id=org_id, user_id=user_id, tier_override=tier)  # type: ignore[assignment]
                    result = await worker.execute(candidate_urls)
                    urls_extracted["resource"] = len(result)
                    total_data += len(result)

                elif dtype == "pricing":
                    from src.workers.pricing_finder import PricingFinderWorker

                    candidate_urls = [u["url"] for u in classified_urls if u.get("data_type") == "pricing"]
                    urls_discovered["pricing"] = len(candidate_urls)
                    worker = PricingFinderWorker(  # type: ignore[assignment]
                        domain=domain, job_id=job_id, pool=pool, org_id=org_id, user_id=user_id, tier_override=tier,
                    )
                    result = await worker.execute(candidate_urls)
                    urls_extracted["pricing"] = len(result)
                    total_data += len(result)

            except Exception as e:
                log.error("worker_error", dtype=dtype, error=str(e))
                errors.append(f"{dtype}: {str(e)}")

        # 4. Mark job complete — include the scraping strategy (tier) used
        duration_ms = int((time.time() - start_time) * 1000)
        from src.db.queries.domains import get_domain_metadata

        domain_meta = await get_domain_metadata(pool, domain)
        strategy = domain_meta.last_successful_strategy if domain_meta else None

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
                # Don't fail job if webhook export fails
                log.error(
                    "webhook_export_error",
                    job_id=job_id,
                    domain=domain,
                    webhook_url=tracked.webhook_url,
                    error=str(e),
                )

        # Build per-dtype yield report for observability
        yield_report = {
            dt: {
                "discovered": urls_discovered.get(dt, 0),
                "extracted": urls_extracted.get(dt, 0),
                "yield_pct": round(
                    urls_extracted.get(dt, 0) / urls_discovered[dt] * 100, 1
                ) if urls_discovered.get(dt) else 0,
            }
            for dt in set(list(urls_discovered.keys()) + list(urls_extracted.keys()))
        }

        log.info(
            "job_completed",
            job_id=job_id,
            domain=domain,
            data_extracted=total_data,
            duration_ms=duration_ms,
            yield_report=yield_report,
            errors=errors if errors else None,
        )

        return {
            "job_id": job_id,
            "domain": domain,
            "data_extracted": total_data,
            "duration_ms": duration_ms,
            "yield_report": yield_report,
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
