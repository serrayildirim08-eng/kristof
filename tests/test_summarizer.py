import pytest

from services import summarizer
from services.summarizer import Budget, BudgetExceeded, Usage


class FakeClient:
    def __init__(self):
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        assert "huurprijs" in user and "<html" not in user
        return {"relevant": True, "priority": "high", "development": "x"}, Usage(1000, 200)


def test_budget_math():
    b = Budget(limit_usd=1.0, spent_usd=0.0, price_in=0.05, price_out=0.08, chars_per_token=3.5)
    assert b.allows(3500, 450)
    cost = b.charge(1000, 200)
    assert abs(cost - (1000 * 0.05e-6 + 200 * 0.08e-6)) < 1e-12 and b.spent == cost


def test_analyze_charges_and_stops_at_budget(settings):
    doc = {"title": "Wet huurprijs", "authority": "Min", "doc_type": "Wet", "published_at": "2026-10-28"}
    client = FakeClient()
    b = Budget(1.0, 0.0, 0.05, 0.08, 3.5)
    result, usage = summarizer.analyze(doc, "de huurprijs stijgt", settings, client, b)
    assert result["relevant"] and usage.cost_usd > 0 and client.calls == 1
    tiny = Budget(0.00001, 0.0, 0.05, 0.08, 3.5)
    with pytest.raises(BudgetExceeded):
        summarizer.analyze(doc, "de huurprijs stijgt", settings, client, tiny)
    assert client.calls == 1  # no call made when over budget
