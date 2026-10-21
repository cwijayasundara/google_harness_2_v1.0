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


def fake_prices(ticker, start, end):
    table = {
        "AAPL": (100.0, 112.0),  # +12%
        "SPY": (100.0, 102.0),  # +2%
        "BTC": (100.0, 112.0),
        "ETH": (100.0, 102.0),
    }
    return table.get(ticker)


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


def test_split_windows():
    from harness_clone.portfolio_score import split_windows

    days = [date(2026, 10, d) for d in (1, 2, 3, 4)]
    evolvable, holdout = split_windows(days)
    assert evolvable == days[:3] and holdout == days[3:]
    evolvable, holdout = split_windows(days, holdout=2)
    assert evolvable == days[:2] and holdout == days[2:]
    assert split_windows(days, holdout=0) == (days, [])
