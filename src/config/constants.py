# Per-request cost estimates by scraping tier
TIER_COSTS: dict[str, float] = {
    "lightpanda": 0.0005,        # Tier 1: lightweight CDP browser, fastest/cheapest
    "basic_http": 0.0001,        # disabled — kept for cost reference only
    "headless_browser": 0.002,
    "playwright": 0.003,         # Tier 2: full Playwright (fallback from lightpanda)
    "playwright_proxy": 0.0035,  # Tier 3: Playwright + residential proxy
    "headless_proxy": 0.004,
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
