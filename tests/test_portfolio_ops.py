import json
from datetime import date

import pytest

from harness_clone.contracts import (
    Candidate,
    ExecutionRecord,
    ExperimentConfig,
    MarketSummary,
    PortfolioPosition,
    PortfolioSnapshot,
    SelectionRecord,
    TaskResult,
)
from harness_clone.portfolio_snapshot import build_snapshot_task
from harness_clone.storage import RunStore

DAY = date(2026, 10, 21)


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


def _summary(ticker, asset_class, price):
    return MarketSummary(
        ticker=ticker,
        asset_class=asset_class,
        last_price=price,
        return_7d_pct=1.0,
        return_30d_pct=10.0,
        volatility_30d=2.0,
        high_52w_distance_pct=-5.0,
    )


@pytest.fixture
def store(tmp_path):
    return RunStore(tmp_path / "run")


def fake_task(base):
    summaries = {"AAPL": _summary("AAPL", "equity", 120.0), "SPY": _summary("SPY", "etf", 500.0)}
    candidates = [Candidate(ticker="MSFT", asset_class="equity", score=9.5, metrics={})]
    return build_snapshot_task(base, DAY, fake_portfolio(), summaries, candidates, 600, [])


def valid_recs():
    return json.dumps(
        [
            {
                "ticker": "AAPL",
                "asset_class": "equity",
                "action": "hold",
                "target_weight_pct": 8.0,
                "horizon_days": 7,
                "thesis": "steady",
                "invalidation": "breaks",
                "benchmark": "SPY",
                "evidence_refs": ["market.json:AAPL.30d_return"],
            }
        ]
    )


class FakeExecutor:
    def __init__(self, artifacts=None, delivered=True):
        self.calls = 0
        self.artifacts = artifacts
        self.delivered = delivered

    def execute(self, task, bundle_digest, execution_id):
        self.calls += 1
        artifacts = self.artifacts
        if artifacts is None:
            artifacts = {
                "recommendations.json": valid_recs(),
                "report.md": f"# Report\n\nBundle digest: {bundle_digest}\nResearch only.\n",
            }
        return ExecutionRecord(
            execution_id=execution_id,
            task_id=task.id,
            bundle_digest=bundle_digest,
            delivered=self.delivered,
            artifacts=artifacts,
        )


@pytest.fixture
def fake_executor():
    return FakeExecutor()


@pytest.fixture
def bad_executor():
    return FakeExecutor(
        artifacts={
            "recommendations.json": json.dumps(
                [
                    {
                        "ticker": "NOPE",
                        "asset_class": "equity",
                        "action": "hold",
                        "target_weight_pct": 8.0,
                        "horizon_days": 7,
                        "thesis": "x",
                        "invalidation": "y",
                        "benchmark": "SPY",
                        "evidence_refs": ["market.json:NOPE"],
                    }
                ]
            ),
            "report.md": "digest " + "d" * 64,
        }
    )


def test_run_daily_executes_once_and_validates(tmp_path, store, fake_executor):
    from harness_clone.portfolio_ops import run_daily

    task = fake_task(tmp_path / "snapshots")
    record = run_daily(store, fake_executor, task, "d" * 64)
    assert fake_executor.calls == 1 and record.delivered is True
    assert record.execution_id == f"{task.id}/daily"
    saved = store.get("execution", record.execution_id)
    assert saved is not None and saved["delivered"] is True


def test_run_daily_rejects_invalid_recommendations(tmp_path, store, bad_executor):
    from harness_clone.portfolio_ops import run_daily

    task = fake_task(tmp_path / "snapshots")
    with pytest.raises(ValueError):
        run_daily(store, bad_executor, task, "d" * 64)
    # Nothing is saved as delivered.
    assert store.get("execution", f"{task.id}/daily") is None


def test_run_daily_accepts_report_without_digest_the_agent_cannot_know(tmp_path, store):
    # The worker never gives the agent its bundle digest, so provenance is
    # stamped host-side rather than required inside the agent's report.
    from harness_clone.portfolio_ops import run_daily

    task = fake_task(tmp_path / "snapshots")
    executor = FakeExecutor(
        artifacts={"recommendations.json": valid_recs(), "report.md": "no provenance here"}
    )
    record = run_daily(store, executor, task, "d" * 64)
    assert record.delivered is True


def test_stamp_report_records_digest_fetch_time_and_age(tmp_path):
    from datetime import datetime, timezone

    from harness_clone.portfolio_ops import stamp_report, write_delivered_report

    task = fake_task(tmp_path / "snapshots")
    record = FakeExecutor(
        artifacts={"recommendations.json": valid_recs(), "report.md": "# Body\n"}
    ).execute(task, "d" * 64, f"{task.id}/daily")
    now = datetime(2026, 10, 21, 10, 30, tzinfo=timezone.utc)
    text = stamp_report(task, record, now)
    assert "Bundle digest: " + "d" * 64 in text
    assert "2026-10-21T08:00:00Z" in text and "2h 30m" in text
    assert text.rstrip().endswith("# Body")
    path = write_delivered_report(task, record, now)
    assert path == tmp_path / "snapshots" / "2026-10-21" / "report.md"
    assert path.read_text() == text
    assert json.loads(path.with_name("recommendations.json").read_text())[0]["ticker"] == "AAPL"


def test_run_daily_rejects_empty_report_and_undelivered(tmp_path, store):
    from harness_clone.portfolio_ops import run_daily

    task = fake_task(tmp_path / "snapshots")
    empty = FakeExecutor(artifacts={"recommendations.json": valid_recs(), "report.md": "  "})
    with pytest.raises(ValueError):
        run_daily(store, empty, task, "d" * 64)
    undelivered = FakeExecutor(delivered=False)
    with pytest.raises(ValueError):
        run_daily(store, undelivered, task, "d" * 64)


def test_evolution_requires_sequential_k1(store):
    from harness_clone.portfolio_ops import run_evolution

    with pytest.raises(ValueError, match="sequential"):
        run_evolution([], ExperimentConfig(mode="parallel"), store, None, None, "d" * 64)
    with pytest.raises(ValueError, match="k=1"):
        run_evolution([], ExperimentConfig(mode="sequential", k=2), store, None, None, "d" * 64)


def test_evolution_delegates_to_run_stream_with_learning(store, monkeypatch):
    import harness_clone.portfolio_ops as ops

    seen = {}

    def fake_run_stream(tasks, config, store_, executor, editor_factory, base_digest, **kw):
        seen.update(tasks=tasks, learn=kw.get("learn"), base=base_digest)
        return ["results"]

    monkeypatch.setattr(ops, "run_stream", fake_run_stream)
    config = ExperimentConfig(mode="sequential", k=1)
    assert ops.run_evolution([], config, store, None, None, "d" * 64) == ["results"]
    assert seen["learn"] is True and seen["base"] == "d" * 64


class FakeProvider:
    def __init__(self):
        self.calls = []

    def closes(self, ticker, asset_class, end, days):
        self.calls.append((ticker, asset_class, end, days))
        base = 100.0
        return [base + i for i in range(40)]


def _fake_sp500_fetch(url):
    return (
        "<table><tr><th>Symbol</th></tr>"
        "<tr><td>AAPL</td></tr><tr><td>MSFT</td></tr><tr><td>NVDA</td></tr></table>"
    )


def _fake_crypto_get(url, params):
    return [
        {"id": "bitcoin", "symbol": "btc"},
        {"id": "ethereum", "symbol": "eth"},
        {"id": "solana", "symbol": "sol"},
    ]


def test_fetch_task_builds_snapshot_with_candidates_and_outcomes(tmp_path):
    from harness_clone.portfolio_ops import fetch_task

    base = tmp_path / "snapshots"
    sb = tmp_path / "sb.json"
    sb.write_text(
        json.dumps(
            {
                "calls": [
                    {
                        "status": "matured",
                        "maturity_date": "2026-10-01",
                        "ticker": "AAPL",
                        "excess_return_pct": 3.0,
                    },
                    {
                        "status": "matured",
                        "maturity_date": "2026-10-25",
                        "ticker": "MSFT",
                        "excess_return_pct": 1.0,
                    },
                ]
            }
        )
    )
    task = fetch_task(
        base,
        DAY,
        fake_portfolio(),
        FakeProvider(),
        scoreboard_path=sb,
        sp500_fetch=_fake_sp500_fetch,
        crypto_get=_fake_crypto_get,
    )
    assert task.id == "portfolio-20261021" and task.domain == "portfolio"
    source = base / "2026-10-21" / "source"
    market = json.loads((source / "market.json").read_text())
    # Holdings plus all four benchmarks.
    assert set(market) == {"AAPL", "SPY", "QQQ", "BTC", "ETH"}
    cand = json.loads((source / "candidates.json").read_text())
    assert cand["universe_considered"] == 6  # 3 S&P + 3 crypto
    assert 0 < cand["universe_returned"] <= 30
    prior = json.loads((source / "prior_outcomes.json").read_text())
    # Only the call matured before the task date leaks in.
    assert [c["ticker"] for c in prior] == ["AAPL"]


def test_fetch_task_without_scoreboard_has_empty_outcomes(tmp_path):
    from harness_clone.portfolio_ops import fetch_task

    task = fetch_task(
        tmp_path / "snapshots",
        DAY,
        fake_portfolio(),
        FakeProvider(),
        sp500_fetch=_fake_sp500_fetch,
        crypto_get=_fake_crypto_get,
    )
    prior = json.loads((task.source_root / "prior_outcomes.json").read_text())
    assert prior == []


def test_ensure_adopted_publishes_only_on_first_use(tmp_path, store):
    from harness_clone.portfolio_ops import ensure_adopted, read_adopted

    from pathlib import Path

    bundle_root = Path(__file__).resolve().parents[1] / "examples" / "base-harness"
    base_dir = tmp_path / "portfolio"
    assert read_adopted(base_dir) is None
    digest = ensure_adopted(store, base_dir, bundle_root)
    assert read_adopted(base_dir) == digest
    # Second call reuses the recorded digest without republishing.
    assert ensure_adopted(store, base_dir, bundle_root) == digest
    adopted = json.loads((base_dir / "adopted.json").read_text())
    assert adopted["bundle_digest"] == digest


def _result(task, label, digest, artifacts, delivered=True):
    record = ExecutionRecord(
        execution_id=f"{task.id}/x/{label}",
        task_id=task.id,
        bundle_digest=digest,
        delivered=delivered,
        artifacts=artifacts,
    )
    return TaskResult(
        task_id=task.id,
        executions={label: record},
        selection=SelectionRecord(chosen_execution_id=record.execution_id),
    )


def test_evolved_digest_adopts_valid_composed_execution(tmp_path):
    from harness_clone.portfolio_ops import evolved_digest

    task = fake_task(tmp_path / "snapshots")
    new_digest = "e" * 64
    artifacts = {
        "recommendations.json": valid_recs(),
        "report.md": f"produced by {new_digest}",
    }
    result = _result(task, "c1", new_digest, artifacts)
    assert evolved_digest([result], [task], "d" * 64) == new_digest


def test_evolved_digest_keeps_previous_on_keep_base_invalid_or_undelivered(tmp_path):
    from harness_clone.portfolio_ops import evolved_digest

    task = fake_task(tmp_path / "snapshots")
    previous = "d" * 64
    # KEEP_BASE: composed execution still carries the previous digest.
    keep = _result(
        task, "c1", previous, {"recommendations.json": valid_recs(), "report.md": previous}
    )
    assert evolved_digest([keep], [task], previous) == previous
    # Schema-invalid recommendations keep the previous bundle.
    bad = _result(task, "c1", "e" * 64, {"recommendations.json": "[]", "report.md": ""})
    # Empty report fails provenance, so previous is kept.
    assert evolved_digest([bad], [task], previous) == previous
    undelivered = _result(
        task,
        "c1",
        "e" * 64,
        {"recommendations.json": valid_recs(), "report.md": "e" * 64},
        delivered=False,
    )
    assert evolved_digest([undelivered], [task], previous) == previous
    assert evolved_digest([], [task], previous) == previous


def test_run_daily_rejects_asset_class_not_matching_snapshot(tmp_path, store):
    from harness_clone.portfolio_ops import run_daily

    task = fake_task(tmp_path / "snapshots")
    recs = json.loads(valid_recs())
    recs[0].update(asset_class="crypto", benchmark="BTC")
    executor = FakeExecutor(
        artifacts={"recommendations.json": json.dumps(recs), "report.md": "d" * 64}
    )
    with pytest.raises(ValueError, match="asset_class"):
        run_daily(store, executor, task, "d" * 64)


class FlakyProvider(FakeProvider):
    """Universe tickers in ``fail`` raise like a 404/429; ``empty`` return nothing."""

    def __init__(self, fail=(), empty=()):
        super().__init__()
        self.fail, self.empty, self.coin_ids = set(fail), set(empty), {}

    def add_coin_ids(self, coin_ids):
        self.coin_ids.update(coin_ids)

    def closes(self, ticker, asset_class, end, days):
        if ticker in self.fail:
            raise RuntimeError(f"HTTP 429 for {ticker}")
        if ticker in self.empty:
            return []
        return super().closes(ticker, asset_class, end, days)


def test_fetch_task_reports_partial_universe_coverage(tmp_path):
    from harness_clone.portfolio_ops import fetch_task

    provider = FlakyProvider(fail={"MSFT", "SOL"}, empty={"NVDA"})
    task = fetch_task(
        tmp_path / "snapshots",
        DAY,
        fake_portfolio(),
        provider,
        sp500_fetch=_fake_sp500_fetch,
        crypto_get=_fake_crypto_get,
    )
    # CoinGecko ids from /coins/markets reach the crypto provider.
    assert provider.coin_ids == {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
    cand = json.loads((task.source_root / "candidates.json").read_text())
    assert cand["universe_considered"] == 6
    missing = cand["universe_missing"]
    assert missing["count"] == 3
    assert missing["sample"] == ["MSFT", "NVDA", "SOL"]
    assert missing["sources_unavailable"] == []
    assert {c["ticker"] for c in cand["candidates"]} == {"AAPL", "BTC", "ETH"}


def test_fetch_task_tolerates_unavailable_sp500_list_but_reports_it(tmp_path):
    from harness_clone.portfolio_ops import fetch_task

    def broken_sp500(url):
        raise RuntimeError("wikipedia unavailable")

    task = fetch_task(
        tmp_path / "snapshots",
        DAY,
        fake_portfolio(),
        FakeProvider(),
        sp500_fetch=broken_sp500,
        crypto_get=_fake_crypto_get,
    )
    cand = json.loads((task.source_root / "candidates.json").read_text())
    assert cand["universe_missing"]["sources_unavailable"] == ["sp500"]
    assert cand["universe_considered"] == 3


def test_fetch_task_holding_fetch_failure_still_aborts(tmp_path):
    from harness_clone.portfolio_ops import fetch_task

    with pytest.raises(RuntimeError, match="AAPL"):
        fetch_task(
            tmp_path / "snapshots",
            DAY,
            fake_portfolio(),
            FlakyProvider(fail={"AAPL"}),
            sp500_fetch=_fake_sp500_fetch,
            crypto_get=_fake_crypto_get,
        )
    assert not (tmp_path / "snapshots" / DAY.isoformat()).exists()


def test_ensure_adopted_imports_archived_bundle_into_a_fresh_store(tmp_path, store):
    from pathlib import Path

    from harness_clone.portfolio_ops import archived_bundle, ensure_adopted

    bundle_root = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "portfolio-harness"
    base_dir = tmp_path / "portfolio"
    digest = ensure_adopted(store, base_dir, bundle_root)
    assert archived_bundle(base_dir, digest).is_dir()
    fresh = RunStore(tmp_path / "week2")
    assert not fresh.bundle_path(digest).exists()
    assert ensure_adopted(fresh, base_dir, tmp_path / "unused") == digest
    assert fresh.bundle_path(digest).is_dir()


def test_ensure_adopted_refuses_tampered_or_missing_archive(tmp_path, store):
    import shutil
    from pathlib import Path

    from harness_clone.portfolio_ops import archived_bundle, ensure_adopted

    bundle_root = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "portfolio-harness"
    base_dir = tmp_path / "portfolio"
    digest = ensure_adopted(store, base_dir, bundle_root)
    archive = archived_bundle(base_dir, digest)
    (archive / "system.md").write_text("tampered")
    with pytest.raises(ValueError, match="digest"):
        ensure_adopted(RunStore(tmp_path / "a"), base_dir, bundle_root)
    shutil.rmtree(archive)
    with pytest.raises(ValueError, match="not archived"):
        ensure_adopted(RunStore(tmp_path / "b"), base_dir, bundle_root)


def test_fetch_task_persists_coin_ids_for_scoring(tmp_path):
    from harness_clone.portfolio_ops import fetch_task

    def crypto_get(url, params):
        return [
            {"id": "bitcoin", "symbol": "btc"},
            {"id": "hyperliquid", "symbol": "hype"},
        ]

    fetch_task(
        tmp_path / "snapshots",
        DAY,
        fake_portfolio(),
        FakeProvider(),
        sp500_fetch=_fake_sp500_fetch,
        crypto_get=crypto_get,
    )
    source = tmp_path / "snapshots" / "2026-10-21" / "source"
    ids = json.loads((source / "coin_ids.json").read_text())
    assert ids["HYPE"] == "hyperliquid"
    assert ids["BTC"] == "bitcoin"
