import json
from datetime import date, timedelta

import pytest

from harness_clone.contracts import PortfolioPosition, PortfolioSnapshot
from harness_clone.portfolio_history import build_history

EPOCH = date(2026, 1, 1)


class FakeProvider:
    """Daily calendar prices; ``closes`` honors ``end`` like a real provider."""

    def __init__(self):
        self.calls = []

    def price_on(self, ticker, day):
        return 100.0 + (day - EPOCH).days

    def dated_closes(self, ticker, asset_class, end, days):
        self.calls.append((ticker, asset_class, end, days))
        series = [
            (EPOCH + timedelta(days=i), self.price_on(ticker, EPOCH + timedelta(days=i)))
            for i in range(400)
        ]
        upto = (end - EPOCH).days + 1
        return series[:upto][-days:]

    def closes(self, ticker, asset_class, end, days):
        return [v for _, v in self.dated_closes(ticker, asset_class, end, days)]


@pytest.fixture
def fake_provider():
    return FakeProvider()


def proxy():
    return PortfolioSnapshot(
        fetched_at="2026-10-21T08:00:00Z",
        currency="USD",
        cash_weight=0.2,
        positions=[
            PortfolioPosition(
                instrument="Apple Inc",
                ticker="AAPL",
                asset_class="equity",
                units=10.0,
                open_rate=100.0,
                current_price=120.0,
                unrealized_pnl_pct=20.0,
                portfolio_weight=0.8,
            )
        ],
    )


def test_history_uses_only_data_up_to_each_day(tmp_path, fake_provider):
    tasks = build_history(tmp_path, [date(2026, 9, 1)], ["AAPL"], fake_provider, proxy())
    market = json.loads((tasks[0].source_root / "market.json").read_text())
    assert market["AAPL"]["last_price"] == fake_provider.price_on("AAPL", date(2026, 9, 1))


def test_history_marks_portfolio_as_proxy(tmp_path, fake_provider):
    tasks = build_history(tmp_path, [date(2026, 9, 1)], ["AAPL"], fake_provider, proxy())
    assert json.loads((tasks[0].source_root / "portfolio.json").read_text())["proxy"] is True


def test_history_requests_closes_bounded_by_each_day(tmp_path, fake_provider):
    days = [date(2026, 9, 1), date(2026, 9, 2)]
    tasks = build_history(tmp_path, days, ["AAPL"], fake_provider, proxy())
    assert [t.id for t in tasks] == ["portfolio-20260901", "portfolio-20260902"]
    assert tasks[0].source_root.parent.name == "2026-09-01"
    assert {call[2] for call in fake_provider.calls} == set(days)
    markets = [
        json.loads((t.source_root / "market.json").read_text()) for t in tasks
    ]
    assert markets[1]["AAPL"]["last_price"] == fake_provider.price_on("AAPL", days[1])
    assert markets[0]["AAPL"]["last_price"] != markets[1]["AAPL"]["last_price"]


def test_history_does_not_mutate_caller_portfolio(tmp_path, fake_provider):
    portfolio = proxy()
    before = portfolio.model_dump(mode="json")
    build_history(tmp_path, [date(2026, 9, 1)], ["AAPL"], fake_provider, portfolio)
    assert portfolio.model_dump(mode="json") == before
    assert "proxy" not in portfolio.model_dump(mode="json")


def test_history_live_snapshot_has_no_proxy_flag_by_default(tmp_path, fake_provider):
    # The proxy flag is opt-in via build_history; a plain live snapshot task
    # built through build_snapshot_task must not gain it.
    from harness_clone.portfolio_snapshot import build_snapshot_task

    task = build_snapshot_task(tmp_path, date(2026, 9, 1), proxy(), {}, [], 0, [])
    assert "proxy" not in json.loads((task.source_root / "portfolio.json").read_text())


def test_history_rejects_series_longer_than_requested_window(tmp_path):
    class LeakyProvider:
        def dated_closes(self, ticker, asset_class, end, days):
            # Ignores the end bound: the series runs past ``end``, so it
            # would leak closes from after the snapshot date.
            return [(end - timedelta(days=i), 100.0) for i in range(3, -3, -1)]

    with pytest.raises(ValueError):
        build_history(tmp_path, [date(2026, 9, 1)], ["AAPL"], LeakyProvider(), proxy())
    assert not (tmp_path / "2026-09-01").exists()


def test_history_empty_ticker_data_is_flagged_missing(tmp_path, fake_provider):
    class EmptyProvider:
        def dated_closes(self, ticker, asset_class, end, days):
            return []

    tasks = build_history(tmp_path, [date(2026, 9, 1)], ["AAPL"], EmptyProvider(), proxy())
    market = json.loads((tasks[0].source_root / "market.json").read_text())
    assert market["AAPL"]["missing"] is True
