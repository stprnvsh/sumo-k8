from datetime import datetime, timezone

from src.cost import estimated_cost_usd


def test_estimated_cost_disabled_returns_none(monkeypatch):
    monkeypatch.setattr("src.cost.get_job_cost_rates", lambda: None)
    s = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    e = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert estimated_cost_usd(2, 4, s, e) is None


def test_estimated_cost_linear(monkeypatch):
    monkeypatch.setattr("src.cost.get_job_cost_rates", lambda: (0.05, 0.01))
    s = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    e = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    # 2h * (2*0.05 + 4*0.01) = 2 * 0.14 = 0.28
    assert estimated_cost_usd(2, 4, s, e) == 0.28
