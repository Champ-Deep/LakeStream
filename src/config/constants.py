# Per-request cost estimates by scraping tier
TIER_COSTS: dict[str, float] = {
    "playwright": 0.003,
    "playwright_proxy": 0.0035,
}

QUEUE_NAMES: dict[str, str] = {
    "scrape_job": "scrape-job",
    "discovery_job": "discovery-job",
}

DEFAULT_JOB_TIMEOUT = 300  # 5 minutes
DEFAULT_JOB_MAX_TRIES = 3
DISCOVERY_JOB_TIMEOUT = 600  # 10 minutes for multi-page search + enqueue

# Deprecated: Firecrawl output directory (kept for backward compatibility)
FIRECRAWL_OUTPUT_DIR = ".firecrawl"
