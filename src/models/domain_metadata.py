from datetime import datetime

from pydantic import BaseModel


class DomainMetadata(BaseModel):
    domain: str
    last_successful_strategy: str | None = None
    block_count: int = 0
    last_scraped_at: datetime | None = None
    success_rate: float | None = None
    avg_cost_usd: float | None = None
    notes: str | None = None
