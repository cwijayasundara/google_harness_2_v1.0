import json
from datetime import date

import pytest

from harness_clone.contracts import Recommendation


def rec(**over):
    base = {
        "ticker": "AAPL",
        "asset_class": "equity",
        "action": "add",
        "target_weight_pct": 10.0,
        "horizon_days": 7,
        "thesis": "growth",
        "invalidation": "falls",
        "benchmark": "SPY",
        "evidence_refs": ["market.json:AAPL"],
    }
    base.update(over)
    return Recommendation(**base)


def fake_prices(ticker, asset_class, start, end):
    table = {
        "AAPL": (100.0, 112.0),  # +12%
        "SPY": (100.0, 102.0),  # +2%
        "BTC": (100.0, 112.0),
        "ETH": (100.0, 102.0),
    }
    return table.get(ticker)


def test_crypto_ticker_routed_by_asset_class():
    from harness_clone.portfolio_score import score_call

    calls = []

    def recording_prices(ticker, asset_class, start, end):
        calls.append((ticker, asset_class))
        if asset_class != "crypto":
            return None
        return {"SOL": (100.0, 120.0), "BTC": (100.0, 110.0)}.get(ticker)

    s = score_call(
        rec(ticker="SOL", asset_class="crypto", benchmark="BTC"),
        date(2026, 10, 14),
        date(2026, 10, 21),
        recording_prices,
        0.2,
    )
    assert s.status == "matured"
    assert calls == [("SOL", "crypto"), ("BTC", "crypto")]
    # +20% vs +10% BTC, minus 0.25 crypto cost.
    assert s.excess_return_pct == pytest.approx(9.75)


def test_unmatured_call_is_pending():
    from harness_clone.portfolio_score import score_call

    assert (
        score_call(rec(), date(2026, 10, 21), date(2026, 10, 25), fake_prices, 0.2).status
        == "pending"
    )


def test_matured_call_scores_excess_net_of_cost():
    from harness_clone.portfolio_score import score_call

    s = score_call(rec(), date(2026, 10, 14), date(2026, 10, 21), fake_prices, 0.2)
    assert s.status == "matured" and s.excess_return_pct == pytest.approx(9.9)
    assert s.maturity_date == date(2026, 10, 21)
    assert s.risk_adjusted == pytest.approx(9.9 / 0.2)


def test_crypto_cost_and_hold_excluded_from_cost():
    from harness_clone.portfolio_score import score_call

    crypto = score_call(
        rec(ticker="BTC", asset_class="crypto", benchmark="ETH"),
        date(2026, 10, 14),
        date(2026, 10, 21),
        fake_prices,
        0.2,
    )
    assert crypto.excess_return_pct == pytest.approx(9.75)
    hold = score_call(rec(action="hold"), date(2026, 10, 14), date(2026, 10, 21), fake_prices, 0.2)
    assert hold.excess_return_pct == pytest.approx(10.0)


def test_missing_prices_unscored_and_zero_volatility():
    from harness_clone.portfolio_score import score_call

    s = score_call(rec(ticker="NOPE"), date(2026, 10, 14), date(2026, 10, 21), fake_prices, 0.2)
    assert s.status == "unscored" and s.excess_return_pct is None
    s2 = score_call(rec(), date(2026, 10, 14), date(2026, 10, 21), fake_prices, 0.0)
    assert s2.status == "matured" and s2.risk_adjusted is None


def _write_run(run_root, rec_dicts, task_date=date(2026, 10, 14)):
    from harness_clone.contracts import ExecutionRecord, SelectionRecord, TaskResult
    from harness_clone.storage import RunStore

    run_root.mkdir(parents=True, exist_ok=True)
    store = RunStore(run_root)
    tid = f"portfolio-{task_date.strftime('%Y%m%d')}"
    ex = ExecutionRecord(
        execution_id="e1",
        task_id=tid,
        bundle_digest="a" * 64,
        delivered=True,
        artifacts={"recommendations.json": json.dumps(rec_dicts)},
    )
    result = TaskResult(
        task_id=tid,
        executions={"e1": ex},
        selection=SelectionRecord(chosen_execution_id="e1"),
    )
    store.put("runtime", "results", [result.model_dump(mode="json")])
    return store


def _rec_dict(**over):
    d = {
        "ticker": "AAPL",
        "asset_class": "equity",
        "action": "add",
        "target_weight_pct": 10.0,
        "horizon_days": 7,
        "thesis": "t",
        "invalidation": "i",
        "benchmark": "SPY",
        "evidence_refs": ["e"],
    }
    d.update(over)
    return d


def test_outcomes_never_leak_into_same_or_earlier_tasks(tmp_path):
    from harness_clone.portfolio_score import outcomes_for, scoreboard

    run_root = tmp_path / "run"
    _write_run(run_root, [_rec_dict()])
    sb = tmp_path / "sb.json"
    scoreboard(run_root, sb, date(2026, 10, 21), fake_prices, {"AAPL": 0.2})
    # maturity = 2026-10-21, strictly before task_date required
    assert outcomes_for(sb, date(2026, 10, 21)) == []
    assert outcomes_for(sb, date(2026, 10, 20)) == []
    assert len(outcomes_for(sb, date(2026, 10, 22))) == 1


def test_scoreboard_refuses_output_inside_run_dir(tmp_path):
    from harness_clone.portfolio_score import scoreboard

    with pytest.raises(ValueError):
        scoreboard(tmp_path, tmp_path / "sb.json", date(2026, 10, 21), fake_prices, {})


def test_aggregates_suppressed_below_20_matured(tmp_path):
    from harness_clone.portfolio_score import scoreboard

    run_root = tmp_path / "run"
    _write_run(run_root, [_rec_dict() for _ in range(3)])
    report = scoreboard(
        run_root, tmp_path / "sb.json", date(2026, 10, 21), fake_prices, {"AAPL": 0.2}
    )
    assert report["matured"] == 3 and report["mean_excess_return_pct"] is None
    assert report["hit_rate"] is None and report["mean_risk_adjusted"] is None
    assert report["active_calls"] == 3


def test_aggregates_at_20_matured_active(tmp_path):
    from harness_clone.portfolio_score import scoreboard

    run_root = tmp_path / "run"
    _write_run(run_root, [_rec_dict() for _ in range(20)])
    report = scoreboard(
        run_root, tmp_path / "sb.json", date(2026, 10, 21), fake_prices, {"AAPL": 0.2}
    )
    assert report["mean_excess_return_pct"] == pytest.approx(9.9)
    assert report["hit_rate"] == pytest.approx(1.0)
    assert report["mean_risk_adjusted"] == pytest.approx(49.5)
    assert report["active_calls"] == 20


def _write_result(run_root, task_id, *, delivered=True, artifacts=None, store_existing=None):
    from harness_clone.contracts import ExecutionRecord, SelectionRecord, TaskResult
    from harness_clone.storage import RunStore

    run_root.mkdir(parents=True, exist_ok=True)
    store = store_existing or RunStore(run_root)
    ex = ExecutionRecord(
        execution_id="e1",
        task_id=task_id,
        bundle_digest="a" * 64,
        delivered=delivered,
        artifacts=(
            {"recommendations.json": json.dumps([_rec_dict()])} if artifacts is None else artifacts
        ),
    )
    result = TaskResult(
        task_id=task_id,
        executions={"e1": ex},
        selection=SelectionRecord(chosen_execution_id="e1"),
    )
    existing = store.get("runtime", "results") or []
    store.put("runtime", "results", existing + [result.model_dump(mode="json")])
    return store


def test_scoreboard_counts_skipped_undeterminable_and_unparseable(tmp_path):
    from harness_clone.portfolio_score import scoreboard

    run_root = tmp_path / "run"
    _write_run(run_root, [_rec_dict()])  # 1 matured call
    from harness_clone.storage import RunStore

    store = RunStore(run_root)
    # Result whose task date cannot be determined.
    _write_result(run_root, "portfolio-nodate", store_existing=store)
    # Result whose recommendations artifact fails to parse.
    _write_result(
        run_root,
        "portfolio-20261014",
        artifacts={"recommendations.json": "{not json"},
        store_existing=store,
    )
    report = scoreboard(
        run_root, tmp_path / "sb.json", date(2026, 10, 21), fake_prices, {"AAPL": 0.2}
    )
    assert report["skipped"] == 2
    assert report["matured"] == 1 and report["pending"] == 0 and report["unscored"] == 0
    # Each fixture result carries one call, so coverage reconciles with 3 results.
    assert report["matured"] + report["pending"] + report["unscored"] + report["skipped"] == 3


def test_scoreboard_counts_skipped_undelivered_and_missing_artifact(tmp_path):
    from harness_clone.portfolio_score import scoreboard

    run_root = tmp_path / "run"
    _write_run(run_root, [_rec_dict()])  # 1 matured call
    from harness_clone.storage import RunStore

    store = RunStore(run_root)
    _write_result(run_root, "portfolio-20261014", delivered=False, store_existing=store)
    _write_result(run_root, "portfolio-20261014", artifacts={}, store_existing=store)
    report = scoreboard(
        run_root, tmp_path / "sb.json", date(2026, 10, 21), fake_prices, {"AAPL": 0.2}
    )
    assert report["skipped"] == 2
    assert report["matured"] == 1
    assert report["matured"] + report["pending"] + report["unscored"] + report["skipped"] == 3


def test_scoreboard_skipped_zero_when_nothing_skipped(tmp_path):
    from harness_clone.portfolio_score import scoreboard

    run_root = tmp_path / "run"
    _write_run(run_root, [_rec_dict()])
    report = scoreboard(
        run_root, tmp_path / "sb.json", date(2026, 10, 21), fake_prices, {"AAPL": 0.2}
    )
    assert report["skipped"] == 0


def test_split_windows():
    from harness_clone.portfolio_score import split_windows

    days = [date(2026, 10, d) for d in (1, 2, 3, 4)]
    evolvable, holdout = split_windows(days)
    assert evolvable == days[:3] and holdout == days[3:]
    evolvable, holdout = split_windows(days, holdout=2)
    assert evolvable == days[:2] and holdout == days[2:]
    assert split_windows(days, holdout=0) == (days, [])


# --- I-3: score windows align to task_date and maturity on dated closes ---
def _trading_series(start, end, price_on):
    """Weekday-only dated closes, like an equity exchange calendar."""
    from datetime import timedelta

    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.append((day, price_on(day)))
        day += timedelta(days=1)
    return out


def test_window_prices_use_close_on_or_before_task_date_and_maturity():
    from harness_clone.portfolio_score import window_prices

    def price_on(day):
        return float(day.toordinal() - date(2026, 10, 1).toordinal() + 100)

    series = _trading_series(date(2026, 9, 1), date(2026, 10, 30), price_on)
    # Sunday task date -> Friday 2026-10-16 close; Sunday maturity -> Friday 10-23.
    start, end = window_prices(series, date(2026, 10, 18), date(2026, 10, 25))
    assert start == price_on(date(2026, 10, 16))
    assert end == price_on(date(2026, 10, 23))
    # Nothing after maturity is ever used, and a missing pre-start close is unscored.
    assert window_prices(series, date(2026, 8, 1), date(2026, 8, 8)) is None
    assert window_prices([], date(2026, 10, 18), date(2026, 10, 25)) is None
    # No close strictly inside the window after the start close: unscored.
    assert window_prices(series, date(2026, 10, 17), date(2026, 10, 18)) is None
