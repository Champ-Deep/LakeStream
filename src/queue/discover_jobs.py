"""arq job functions for the discovery pipeline."""

from datetime import datetime
from uuid import UUID

import structlog

from src.config.settings import get_settings
from src.db.queries import discovery as disc_queries
from src.db.queries import jobs as job_queries
from src.models.discovery import DiscoveryJobInput, DiscoveryStatus
from src.models.job import ScrapeJobInput
from src.services.domain_extractor import extract_unique_domains
from src.services.lakecurrent import LakeCurrentClient

log = structlog.get_logger()


async def process_discovery_job(ctx: dict, *, discovery_id: str) -> dict:
    """Search LakeCurrent, extract domains, enqueue child scrape jobs."""
    pool = ctx["pool"]
    settings = get_settings()
    uid = UUID(discovery_id)

    job = await disc_queries.get_discovery_job(pool, uid)
    if job is None:
        log.error("discovery_job_not_found", discovery_id=discovery_id)
        return {"error": "discovery job not found"}

    log.info("discovery_job_started", discovery_id=discovery_id, query=job.query)

    client = LakeCurrentClient(
        base_url=settings.lakecurrent_base_url,
        timeout=settings.lakecurrent_timeout,
    )

    try:
        # 1. Search LakeCurrent for results across multiple pages
        results = await client.search_pages(
            job.query,
            pages=job.search_pages,
            per_page=job.results_per_page,
            mode=job.search_mode,
        )

        # 2. Store raw search results for reference
        raw_results = [r.model_dump() for r in results]
        await disc_queries.update_discovery_status(
            pool, uid, DiscoveryStatus.SEARCHING, search_results=raw_results
        )

        # 3. Extract unique domains, filtering recently scraped
        skip_domains = await disc_queries.get_recently_scraped_domains(
            pool, days=settings.discovery_skip_recent_days
        )
        domain_map = extract_unique_domains(results, skip_domains=skip_domains)

        # Cap at max domains per query
        max_domains = settings.discovery_max_domains_per_query
        domain_items = list(domain_map.items())[:max_domains]

        domains_found = len(domain_map)
        domains_skipped = 0

        # 4. Create child scrape jobs for each unique domain
        for domain, result in domain_items:
            # Create scrape_job
            scrape_input = ScrapeJobInput(
                domain=domain,
                template_id=job.template_id if job.template_id != "generic" else None,
                max_pages=job.max_pages_per_domain,
                data_types=job.data_types,
            )
            scrape_job = await job_queries.create_job(pool, scrape_input)

            # Record in discovery_job_domains
            await disc_queries.insert_discovery_domain(
                pool,
                discovery_id=uid,
                domain=domain,
                source_url=result.url,
                source_title=result.title,
                source_snippet=result.snippet,
                source_score=result.score,
                scrape_job_id=scrape_job.id,
                status="scraping",
            )

            # Enqueue the scrape job via arq
            redis = ctx.get("redis")
            if redis:
                await redis.enqueue_job(
                    "process_scrape_job",
                    job_id=str(scrape_job.id),
                    domain=domain,
                    template_id=job.template_id or "auto",
                    max_pages=job.max_pages_per_domain,
                    data_types=job.data_types,
                )

        # 5. Record skipped domains
        all_domains_in_results = {r.domain for r in results}
        skipped = all_domains_in_results & skip_domains
        domains_skipped = len(skipped)
        for domain in skipped:
            # Find the best result for the skipped domain
            best = None
            for r in results:
                if r.domain == domain:
                    if best is None or (r.score or 0) > (best.score or 0):
                        best = r
            if best:
                await disc_queries.insert_discovery_domain(
                    pool,
                    discovery_id=uid,
                    domain=domain,
                    source_url=best.url,
                    source_title=best.title,
                    source_snippet=best.snippet,
                    source_score=best.score,
                    status="skipped",
                    skip_reason="recently scraped",
                )

        # 6. Update discovery job status
        new_status = DiscoveryStatus.SCRAPING if domain_items else DiscoveryStatus.COMPLETED
        await disc_queries.update_discovery_status(
            pool,
            uid,
            new_status,
            domains_found=domains_found,
            domains_skipped=domains_skipped,
            completed_at=datetime.now() if not domain_items else None,
        )

        log.info(
            "discovery_job_domains_enqueued",
            discovery_id=discovery_id,
            domains_found=domains_found,
            domains_enqueued=len(domain_items),
            domains_skipped=domains_skipped,
        )

        return {
            "discovery_id": discovery_id,
            "domains_found": domains_found,
            "domains_enqueued": len(domain_items),
            "domains_skipped": domains_skipped,
        }

    except Exception as e:
        await disc_queries.update_discovery_status(
            pool,
            uid,
            DiscoveryStatus.FAILED,
            error_message=str(e),
        )
        log.error("discovery_job_failed", discovery_id=discovery_id, error=str(e))
        raise
    finally:
        await client.close()


async def check_tracked_searches(ctx: dict) -> None:
    """Cron job: find due tracked searches and create discovery jobs for them."""
    from src.db.pool import get_pool

    pool = await get_pool()
    due = await disc_queries.get_due_tracked_searches(pool)

    if not due:
        return

    log.info("tracked_search_check", due_count=len(due))

    for tracked in due:
        try:
            # Create a discovery job from the tracked search params
            disc_input = DiscoveryJobInput(
                query=tracked.query,
                search_mode=tracked.search_mode,
                search_pages=tracked.search_pages,
                results_per_page=tracked.results_per_page,
                data_types=tracked.data_types,
                template_id=tracked.template_id,
                max_pages_per_domain=tracked.max_pages_per_domain,
            )
            disc_job = await disc_queries.create_discovery_job(
                pool, disc_input, str(tracked.org_id)
            )

            # Enqueue the discovery job
            redis = ctx.get("redis")
            if redis:
                await redis.enqueue_job(
                    "process_discovery_job",
                    discovery_id=str(disc_job.id),
                )

            await disc_queries.mark_tracked_search_run(pool, tracked.id)

            log.info(
                "tracked_search_enqueued",
                tracked_id=str(tracked.id),
                discovery_id=str(disc_job.id),
                query=tracked.query,
            )
        except Exception:
            log.exception("tracked_search_failed", tracked_id=str(tracked.id))
