"""CLI-level proof that the weekly evolution loop runs past week 1 (C-1, I-2).

Everything runs through ``harness_clone.cli.main`` with fake live
components: no Docker, network, model, or real credential. Two consecutive
ISO weeks of snapshots go through three ``daily`` runs and two ``evolve``
runs; week 2 must evolve from week 1's adopted bundle in a fresh run
directory, and the forward record must hold only delivered daily results.
"""

import json
from datetime import date, timedelta

import pytest
from test_portfolio_integration import FakeEditor, FakeExecutor, _portfolio, _summary

import harness_clone.cli as cli
from harness_clone.contracts import Candidate
from harness_clone.portfolio_ops import read_adopted
from harness_clone.portfolio_snapshot import build_snapshot_task
from harness_clone.storage import RunStore

WEEK1 = [date(2026, 10, 12) + timedelta(days=i) for i in range(5)]
WEEK2 = [date(2026, 10, 19) + timedelta(days=i) for i in range(5)]


@pytest.fixture
def loop(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(cli, "portfolio_bundle", lambda: Path(__file__).parent / "fixtures/portfolio-harness")
    base = tmp_path / "portfolio"
    summaries = {"AAPL": _summary("AAPL", "equity", 120.0), "SPY": _summary("SPY", "etf", 500.0)}
    candidates = [Candidate(ticker="MSFT", asset_class="equity", score=9.5, metrics={})]
    for day in WEEK1 + WEEK2:
        build_snapshot_task(base, day, _portfolio(), summaries, candidates, 600, [])

    executor = FakeExecutor()
    seen: dict[str, set[str]] = {}
    built_for: list[str] = []

    def fake_live_components(config, store):
        built_for.append(str(store.root))
        return executor, lambda task: FakeEditor(task.id, seen)

    monkeypatch.setenv("FIREWORKS_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr(cli, "docker_available", lambda: True)
    monkeypatch.setattr(cli, "resolve_image_id", lambda tag: "sha256:" + "0" * 64)
    monkeypatch.setattr(cli, "live_components", fake_live_components)

    def run(command, day, *extra):
        argv = ["portfolio", command, "--base-dir", str(base), "--date", day.isoformat()]
        if command in {"daily", "evolve"}:
            argv += ["--max-usd", "2"]
        code = cli.main([*argv, *extra])
        return code

    return base, run, executor, built_for


def _out(capsys):
    return json.loads(capsys.readouterr().out)


def test_two_weeks_of_daily_and_evolve_through_the_cli(loop, capsys):
    base, run, executor, built_for = loop

    assert run("daily", WEEK1[2]) == 0
    first_daily = _out(capsys)
    base_digest = first_daily["bundle_digest"]
    assert run("daily", WEEK1[3]) == 0
    capsys.readouterr()

    assert run("evolve", WEEK1[4]) == 0
    week1 = _out(capsys)
    assert week1["previous_bundle_digest"] == base_digest
    adopted1 = week1["adopted_bundle_digest"]
    assert adopted1 != base_digest
    assert read_adopted(base) == adopted1
    # Evolution covers exactly the ISO week's five snapshots.
    assert week1["tasks"] == 5

    assert run("daily", WEEK2[0]) == 0
    week2_daily = _out(capsys)
    assert week2_daily["bundle_digest"] == adopted1
    stamped = (base / WEEK2[0].isoformat() / "report.md").read_text()
    assert f"Bundle digest: {adopted1}" in stamped

    # Week 2 evolves from week 1's adopted bundle in its own run directory.
    assert run("evolve", WEEK2[4]) == 0, "week 2 evolution must not fail"
    week2 = _out(capsys)
    assert week2["previous_bundle_digest"] == adopted1
    assert week2["tasks"] == 5
    evolve_dirs = sorted({p for p in built_for if "evolve" in p})
    assert len(evolve_dirs) == 2

    # I-2: the forward record holds only the three delivered daily results.
    forward = RunStore(base / "forward")
    results = forward.get("runtime", "results")
    assert sorted(r["task_id"] for r in results) == [
        "portfolio-20261014",
        "portfolio-20261015",
        "portfolio-20261019",
    ]
    assert all(list(r["executions"]) == ["daily"] for r in results)


def test_rerunning_an_old_week_does_not_regress_adoption(loop, capsys):
    base, run, _executor, _built = loop
    assert run("evolve", WEEK1[4]) == 0
    adopted1 = _out(capsys)["adopted_bundle_digest"]
    assert run("evolve", WEEK2[4]) == 0
    adopted2 = read_adopted(base)
    capsys.readouterr()
    # Resuming week 1 replays from its original base and must not overwrite
    # the newer adoption.
    assert run("evolve", WEEK1[4]) == 0
    rerun = _out(capsys)
    assert rerun["adopted_bundle_digest"] == adopted1
    assert read_adopted(base) == adopted2


def test_each_daily_run_has_its_own_run_dir_and_spend_cap(loop, capsys):
    base, run, _executor, built_for = loop
    for day in WEEK1[:3]:
        assert run("daily", day) == 0
    capsys.readouterr()
    daily_dirs = [p for p in built_for if "daily" in p]
    assert len(set(daily_dirs)) == 3
    # A daily cap is per invocation, not a lifetime cap on one shared ledger.
    assert all((base / "runs" / f"daily-{d.isoformat()}").is_dir() for d in WEEK1[:3])


def test_score_reads_the_forward_record_by_default(loop, capsys, monkeypatch, tmp_path):
    base, run, _executor, _built = loop
    assert run("daily", WEEK1[0]) == 0
    assert run("evolve", WEEK1[4]) == 0
    capsys.readouterr()
    seen = {}

    def fake_scoreboard(run_root, out_path, today, prices, volatilities):
        seen["run_root"] = run_root
        return {"generated_at": today.isoformat(), "matured": 0, "pending": 0}

    monkeypatch.setattr(cli, "portfolio_scoreboard", fake_scoreboard)
    class NoPrices:
        def closes(self, ticker, asset_class, end, days):
            return []

        def dated_closes(self, ticker, asset_class, end, days):
            return []

    monkeypatch.setattr(cli, "live_price_provider", lambda: NoPrices())
    out = tmp_path / "scoreboard.json"
    assert run("score", WEEK2[0], "--scoreboard", str(out)) == 0
    assert seen["run_root"] == base / "forward"
