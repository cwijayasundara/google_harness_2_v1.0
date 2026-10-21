import json
from datetime import date

import pytest

from harness_clone.contracts import (
    Candidate,
    MarketSummary,
    PortfolioPosition,
    PortfolioSnapshot,
)
from harness_clone.portfolio_snapshot import build_snapshot_task
from harness_clone.tasks import load_task


@pytest.fixture
def fake_portfolio():
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


@pytest.fixture
def fake_summaries():
    return {
        "AAPL": MarketSummary(
            ticker="AAPL",
            asset_class="equity",
            last_price=120.0,
            return_7d_pct=1.0,
            return_30d_pct=10.0,
            volatility_30d=2.0,
            high_52w_distance_pct=-5.0,
        ),
        "SPY": MarketSummary(
            ticker="SPY",
            asset_class="etf",
            last_price=500.0,
            return_7d_pct=0.5,
            return_30d_pct=3.0,
            volatility_30d=1.0,
            high_52w_distance_pct=-2.0,
        ),
    }


def test_build_writes_loadable_task(tmp_path, fake_portfolio, fake_summaries):
    task = build_snapshot_task(tmp_path, date(2026, 10, 21), fake_portfolio, fake_summaries, [], 600, [])
    assert task.domain == "portfolio" and task.ordinal == 20261021
    assert load_task(tmp_path / "2026-10-21" / "task.json") == task


def test_build_records_universe_coverage(tmp_path, fake_portfolio, fake_summaries):
    build_snapshot_task(tmp_path, date(2026, 10, 21), fake_portfolio, fake_summaries, [], 600, [])
    data = json.loads((tmp_path / "2026-10-21/source/candidates.json").read_text())
    assert data["universe_considered"] == 600 and data["universe_returned"] == 0


def test_build_refuses_overwrite_and_oversize(tmp_path, fake_portfolio, fake_summaries):
    build_snapshot_task(tmp_path, date(2026, 10, 21), fake_portfolio, fake_summaries, [], 600, [])
    with pytest.raises(ValueError):
        build_snapshot_task(tmp_path, date(2026, 10, 21), fake_portfolio, fake_summaries, [], 600, [])
    # A single file over 1 MB is refused.
    with pytest.raises(ValueError):
        build_snapshot_task(
            tmp_path, date(2026, 10, 22), fake_portfolio, fake_summaries, [], 600,
            [{"note": "x" * 1_100_000}],
        )


def test_build_writes_all_sources_and_task_fields(tmp_path, fake_portfolio, fake_summaries):
    candidates = [
        Candidate(ticker="MSFT", asset_class="equity", score=9.5, metrics={"return_30d_pct": 12.0})
    ]
    prior = [{"ticker": "AAPL", "excess_return_pct": 3.2}]
    task = build_snapshot_task(
        tmp_path, date(2026, 10, 21), fake_portfolio, fake_summaries, candidates, 600, prior
    )
    assert task.id == "portfolio-20261021"
    assert task.output_paths == ["recommendations.json", "report.md"]
    source = tmp_path / "2026-10-21" / "source"
    assert task.source_root == source.resolve()
    portfolio = json.loads((source / "portfolio.json").read_text())
    assert portfolio["currency"] == "USD"
    assert portfolio["positions"][0]["ticker"] == "AAPL"
    market = json.loads((source / "market.json").read_text())
    assert market["AAPL"]["last_price"] == 120.0
    assert market["SPY"]["ticker"] == "SPY"
    cand = json.loads((source / "candidates.json").read_text())
    assert cand["universe_considered"] == 600 and cand["universe_returned"] == 1
    assert cand["candidates"][0]["ticker"] == "MSFT"
    assert cand["candidates"][0]["metrics"]["return_30d_pct"] == 12.0
    assert json.loads((source / "prior_outcomes.json").read_text()) == prior
    # Prompt instructs the recommendation schema and report structure (spec 5.2).
    for needle in (
        "recommendations.json", "report.md", "ticker", "asset_class",
        "hold|trim|add|new_idea|exit", "target_weight_pct", "horizon_days",
        "thesis", "invalidation", "benchmark", "evidence_refs",
        "portfolio", "risk", "research-only", "snapshot",
    ):
        assert needle.lower() in task.prompt.lower(), needle


def test_build_refuses_total_over_2mb(tmp_path, fake_portfolio, fake_summaries):
    # Three files each under 1 MB but totalling over 2 MB.
    big = "x" * 700_000
    portfolio = fake_portfolio.model_copy(
        update={"positions": [fake_portfolio.positions[0].model_copy(update={"instrument": big})]}
    )
    with pytest.raises(ValueError):
        build_snapshot_task(
            tmp_path, date(2026, 10, 21), portfolio, fake_summaries,
            [Candidate(ticker=big, asset_class="equity", score=1.0, metrics={})],
            600,
            [{"note": big}],
        )


def test_oversize_attempt_leaves_no_snapshot(tmp_path, fake_portfolio, fake_summaries):
    with pytest.raises(ValueError):
        build_snapshot_task(
            tmp_path, date(2026, 10, 21), fake_portfolio, fake_summaries, [], 600,
            [{"note": "x" * 1_100_000}],
        )
    assert not (tmp_path / "2026-10-21").exists()
