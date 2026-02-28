from src.services.cost_tracker import CostTracker


class TestCostTracker:
    def test_record_basic_http(self):
        t = CostTracker()
        assert t.record_cost("j1", "ex.com", "basic_http") == 0.0001

    def test_accumulates(self):
        t = CostTracker()
        t.record_cost("j1", "ex.com", "basic_http")
        t.record_cost("j1", "ex.com", "headless_browser")
        assert t.get_job_cost("j1") == 0.0001 + 0.002

    def test_budget_ok(self):
        t = CostTracker()
        t.record_cost("j1", "ex.com", "basic_http")
        assert t.check_budget("j1", max_job_cost=1.0) is True

    def test_budget_exceeded(self):
        t = CostTracker()
        t._job_costs["j1"] = 1.5
        assert t.check_budget("j1", max_job_cost=1.0) is False

    def test_unknown_tier(self):
        t = CostTracker()
        assert t.record_cost("j1", "ex.com", "unknown") == 0.0
