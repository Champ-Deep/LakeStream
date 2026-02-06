from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    port: int = 3001
    host: str = "0.0.0.0"
    debug: bool = False
    log_level: str = "info"

    database_url: str = "postgresql://scraper:scraper_dev@localhost:5433/lakeb2b_scraper"
    redis_url: str = "redis://localhost:6379"

    brightdata_proxy_url: str = ""
    smartproxy_url: str = ""
    firecrawl_api_key: str = ""

    max_concurrent_jobs: int = 10
    max_scrape_pages_per_job: int = 500
    default_rate_limit_ms: int = 1000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
