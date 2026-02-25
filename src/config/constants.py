TIER_COSTS: dict[str, float] = {
    "basic_http": 0.0001,
    "headless_browser": 0.002,
    "headless_proxy": 0.004,
}

QUEUE_NAMES: dict[str, str] = {
    "scrape_job": "scrape-job",
    "discovery_job": "discovery-job",
}

DEFAULT_JOB_TIMEOUT = 300  # 5 minutes
DEFAULT_JOB_MAX_TRIES = 3
DISCOVERY_JOB_TIMEOUT = 600  # 10 minutes for multi-page search + enqueue

# Firecrawl output directory
FIRECRAWL_OUTPUT_DIR = ".firecrawl"
