import os
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    port: int = 3001
    host: str = "0.0.0.0"
    debug: bool = False
    log_level: str = "info"
    base_url: str = ""

    database_url: str = "postgresql://scraper:scraper_dev@localhost:5433/lakeb2b_scraper"
    redis_url: str = "redis://localhost:6379"

    brightdata_proxy_url: str = ""
    smartproxy_url: str = ""
    firecrawl_api_key: str = ""

    max_concurrent_jobs: int = 10
    max_scrape_pages_per_job: int = 500
    default_rate_limit_ms: int = 1000

    # Authentication & Multi-tenancy
    jwt_secret: str = Field(..., description="JWT signing secret (required, use: openssl rand -hex 32)")
    jwt_algorithm: str = "HS256"
    access_token_expire_hours: int = 24

    # Multi-tenancy limits
    default_max_users_per_org: int = 5
    default_max_domains_per_org: int = 10

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @model_validator(mode="after")
    def fix_postgres_scheme(self) -> "Settings":
        # Railway/Heroku provide postgres:// but asyncpg requires postgresql://
        if self.database_url.startswith("postgres://"):
            self.database_url = self.database_url.replace(
                "postgres://", "postgresql://", 1
            )
        return self

    @model_validator(mode="after")
    def compute_base_url(self) -> "Settings":
        """Auto-detect public URL from Railway env or fall back to localhost."""
        if not self.base_url:
            railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
            if railway_domain:
                self.base_url = f"https://{railway_domain}"
            else:
                self.base_url = f"http://localhost:{self.port}"
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
