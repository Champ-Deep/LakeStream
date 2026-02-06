import structlog

from src.config.constants import TIER_COSTS

log = structlog.get_logger()


class CostTracker:
    """Tracks per-job and per-domain scraping costs."""

    def __init__(self) -> None:
        self._job_costs: dict[str, float] = {}
        self._domain_costs: dict[str, float] = {}

    def record_cost(self, job_id: str, domain: str, tier: str) -> float:
        """Record a single request cost."""
        cost = TIER_COSTS.get(tier, 0.0)

        self._job_costs[job_id] = self._job_costs.get(job_id, 0.0) + cost
        self._domain_costs[domain] = self._domain_costs.get(domain, 0.0) + cost

        return cost

    def get_job_cost(self, job_id: str) -> float:
        return self._job_costs.get(job_id, 0.0)

    def get_domain_cost(self, domain: str) -> float:
        return self._domain_costs.get(domain, 0.0)

    def check_budget(
        self,
        job_id: str,
        max_job_cost: float = 1.0,
    ) -> bool:
        """Check if a job is within budget. Returns False if over budget."""
        current = self.get_job_cost(job_id)
        if current >= max_job_cost:
            log.warning(
                "budget_exceeded",
                job_id=job_id,
                current_cost=current,
                max_cost=max_job_cost,
            )
            return False
        return True
