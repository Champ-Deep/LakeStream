import structlog

from src.scraping.parser.url_classifier import classify_urls
from src.scraping.validator.url_validator import validate_and_deduplicate
from src.services.crawler import CrawlerService
from src.utils.url import ensure_scheme
from src.workers.job_lifecycle import JobLifecycle

log = structlog.get_logger()


class DomainMapperWorker:
    """Discovers all URLs for a domain using CrawlerService, then classifies them.

    This is intentionally **not** a ``BaseWorker`` subclass. ``BaseWorker``'s
    contract (tier-escalating ``fetch_page``, per-record ``export_results``,
    ``list[ScrapedData]`` output) describes a content-extraction worker that
    fetches pages and persists extracted records. ``DomainMapperWorker`` does
    neither: it delegates all fetching to ``CrawlerService`` (its own
    concurrency/politeness model, not the escalation/rate-limiter stack) and
    returns plain classified-URL dicts that are handed directly to
    ``ContentWorker`` — nothing here is ever persisted on its own. Forcing it
    to inherit ``BaseWorker`` would mean accepting and ignoring most of that
    constructor (template, tier_override, proxy_url, region, raw_only) and
    exposing an ``execute()``/``export_results()`` shape it doesn't use.

    What *is* shared with ``BaseWorker`` — and must behave identically — is
    heartbeat throttling during long-running work, so that emitting one
    heartbeat per discovered URL doesn't hammer the DB. That behavior is
    factored out into ``JobLifecycle`` (see ``src/workers/job_lifecycle.py``),
    which both this worker and ``ContentWorker`` use.
    """

    def __init__(
        self, domain: str, job_id: str, org_id: str | None = None, pool=None,
        user_id: str | None = None,
    ):
        self.domain = domain
        self.job_id = job_id
        self.org_id = org_id
        self.user_id = user_id
        self.pool = pool
        self.crawler = CrawlerService(
            max_concurrent=15, max_per_domain=6, pool=pool, job_id=job_id, user_id=user_id,
        )
        self.log = log.bind(worker="DomainMapper", domain=domain, job_id=job_id)
        self._lifecycle = JobLifecycle(pool, job_id, logger=self.log)

    async def execute(self, max_pages: int | None = None) -> list[dict]:
        """Map a domain and return classified URLs.

        Args:
            max_pages: Maximum pages to crawl (None for unlimited)

        Returns:
            List of classified URLs with data_type annotations
        """
        self.log.info("mapping_domain", max_pages=max_pages or "unlimited")

        # Signal the job is still alive before starting the potentially long
        # crawl. Time-throttled (same 30s-min-interval semantics as
        # BaseWorker.heartbeat) so repeated calls across a long crawl don't
        # hammer the DB.
        await self._lifecycle.heartbeat()

        # 1. Discover URLs via CrawlerService (unlimited by default)
        url = ensure_scheme(self.domain)
        raw_urls = await self.crawler.map_domain(url, limit=max_pages)

        # Heartbeat after crawl completes (may have taken minutes)
        await self._lifecycle.heartbeat()
        self.log.info("urls_discovered", count=len(raw_urls))

        # Fallback: always include the homepage.
        # The crawler uses a cheap HTTP fetcher that gets blocked by many sites,
        # but ContentWorker uses Playwright with escalation and can often still
        # extract content from the homepage directly.
        homepage = ensure_scheme(self.domain)
        if homepage not in raw_urls:
            raw_urls.insert(0, homepage)
            self.log.info("homepage_fallback_added", url=homepage)

        # 2. Validate and deduplicate (keep duplicates from traversal if max_pages is None)
        valid_urls = validate_and_deduplicate(raw_urls)
        self.log.info("urls_validated", count=len(valid_urls))

        # 3. Classify by data type
        classified = classify_urls(valid_urls)
        self.log.info(
            "urls_classified",
            total=len(classified),
            types={
                dt: len([c for c in classified if c["data_type"] == dt])
                for dt in set(c["data_type"] for c in classified)
            },
        )

        return classified
