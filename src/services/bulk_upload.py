"""Bulk CSV upload service — parse, validate, deduplicate, and enqueue domains.

Responsible only for CSV handling and staggered job creation.
The actual scraping is handled by the existing process_scrape_job pipeline.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from uuid import UUID

import structlog

log = structlog.get_logger()

MAX_URLS_PER_UPLOAD = 100
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
STAGGER_DELAY_SECONDS = 30  # seconds between each job start


@dataclass
class ParsedURL:
    raw: str
    domain: str
    valid: bool
    skip_reason: str = ""


@dataclass
class BulkParseResult:
    valid: list[ParsedURL] = field(default_factory=list)
    invalid: list[ParsedURL] = field(default_factory=list)
    duplicates_in_file: int = 0
    already_queued: list[str] = field(default_factory=list)
    error: str = ""


def _normalize_domain(raw: str) -> str:
    """Extract a clean domain from user input (URL, domain, or garbage)."""
    raw = raw.strip().strip('"').strip("'")
    if not raw:
        return ""

    # Remove protocol
    raw = re.sub(r"^https?://", "", raw)
    # Remove trailing slash and path
    domain = raw.split("/")[0].strip()
    # Remove www. prefix for dedup (but keep it for the actual scrape)
    return domain


def _is_valid_domain(domain: str) -> bool:
    """Basic domain validation."""
    if not domain or len(domain) < 3:
        return False
    if "." not in domain:
        return False
    if " " in domain:
        return False
    # Must have at least one letter
    if not re.search(r"[a-zA-Z]", domain):
        return False
    return True


def parse_bulk_csv(file_content: bytes, filename: str = "") -> BulkParseResult:
    """Parse a CSV file and extract valid domains.

    Handles:
    - Single column CSV (just URLs)
    - Multi-column CSV (looks for 'url', 'domain', 'website' column)
    - With or without header row
    - Deduplication within the file
    """
    result = BulkParseResult()

    if len(file_content) > MAX_FILE_SIZE_BYTES:
        result.error = f"File too large ({len(file_content) // 1024}KB). Maximum is {MAX_FILE_SIZE_BYTES // 1024}KB."
        return result

    try:
        text = file_content.decode("utf-8-sig")  # Handle BOM
    except UnicodeDecodeError:
        try:
            text = file_content.decode("latin-1")
        except Exception:
            result.error = "Could not decode file. Please use UTF-8 encoding."
            return result

    if not text.strip():
        result.error = "File is empty."
        return result

    # Parse CSV
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)

    if not rows:
        result.error = "No rows found in CSV."
        return result

    # Detect which column has the URLs
    url_col_idx = 0
    header = rows[0]
    has_header = False

    # Check if first row looks like a header
    url_header_names = {"url", "domain", "website", "site", "link", "urls", "domains"}
    for i, cell in enumerate(header):
        if cell.strip().lower() in url_header_names:
            url_col_idx = i
            has_header = True
            break

    # If no header detected but first row doesn't look like a domain, assume header
    if not has_header and header:
        first_val = _normalize_domain(header[url_col_idx] if url_col_idx < len(header) else "")
        if not _is_valid_domain(first_val):
            has_header = True

    data_rows = rows[1:] if has_header else rows

    # Extract and validate domains
    seen_domains: set[str] = set()

    for row in data_rows:
        if not row or url_col_idx >= len(row):
            continue

        raw = row[url_col_idx].strip()
        if not raw:
            continue

        domain = _normalize_domain(raw)

        if not _is_valid_domain(domain):
            result.invalid.append(ParsedURL(raw=raw, domain=domain, valid=False, skip_reason="Invalid domain"))
            continue

        # Dedup within file (case-insensitive)
        domain_lower = domain.lower().removeprefix("www.")
        if domain_lower in seen_domains:
            result.duplicates_in_file += 1
            continue
        seen_domains.add(domain_lower)

        result.valid.append(ParsedURL(raw=raw, domain=domain, valid=True))

    # Enforce max URLs limit
    if len(result.valid) > MAX_URLS_PER_UPLOAD:
        excess = len(result.valid) - MAX_URLS_PER_UPLOAD
        result.valid = result.valid[:MAX_URLS_PER_UPLOAD]
        result.error = f"CSV contained {len(result.valid) + excess} valid URLs. Trimmed to {MAX_URLS_PER_UPLOAD} (max per upload)."

    return result


async def check_already_queued(pool, domains: list[str]) -> set[str]:
    """Check which domains already have pending/running jobs in the last hour."""
    if not domains:
        return set()

    rows = await pool.fetch(
        """
        SELECT DISTINCT domain FROM scrape_jobs
        WHERE status IN ('pending', 'running')
          AND created_at > NOW() - INTERVAL '1 hour'
          AND domain = ANY($1::text[])
        """,
        domains,
    )
    return {row["domain"] for row in rows}


async def enqueue_bulk_jobs(
    pool,
    domains: list[str],
    *,
    org_id: UUID,
    user_id: UUID,
    max_pages: int = 100,
    data_types: list[str] | None = None,
) -> list[dict]:
    """Create job records and enqueue them with staggered delays.

    Returns list of {job_id, domain, status} for each enqueued job.
    """
    from arq.connections import RedisSettings
    from arq.connections import create_pool as create_arq_pool

    from src.config.settings import get_settings
    from src.db.queries.jobs import create_job
    from src.models.job import ScrapeJobInput

    if data_types is None:
        data_types = ["blog_url", "article", "contact", "tech_stack", "resource", "pricing"]

    settings = get_settings()
    results = []

    try:
        redis = await create_arq_pool(RedisSettings.from_dsn(settings.redis_url))
    except Exception as e:
        log.error("bulk_upload_redis_connect_failed", error=str(e))
        return [{"domain": d, "status": "error", "error": "Redis connection failed"} for d in domains]

    try:
        for i, domain in enumerate(domains):
            try:
                job_input = ScrapeJobInput(
                    domain=domain,
                    max_pages=max_pages,
                    data_types=data_types,
                )
                job = await create_job(pool, job_input, org_id=org_id, user_id=user_id)

                # Stagger: each job starts N*30s after the previous one
                defer_seconds = i * STAGGER_DELAY_SECONDS

                await redis.enqueue_job(
                    "process_scrape_job",
                    job_id=str(job.id),
                    domain=domain,
                    template_id="auto",
                    max_pages=max_pages,
                    data_types=data_types,
                    _defer_by=defer_seconds,
                )

                results.append({
                    "job_id": str(job.id),
                    "domain": domain,
                    "status": "queued",
                    "defer_seconds": defer_seconds,
                })
                log.info("bulk_job_enqueued", domain=domain, job_id=str(job.id), defer_seconds=defer_seconds)

            except Exception as e:
                log.error("bulk_job_enqueue_failed", domain=domain, error=str(e))
                results.append({"domain": domain, "status": "error", "error": str(e)})
    finally:
        await redis.aclose()

    log.info(
        "bulk_upload_complete",
        total=len(domains),
        queued=sum(1 for r in results if r["status"] == "queued"),
        failed=sum(1 for r in results if r["status"] == "error"),
    )
    return results
