"""End-to-end offline proof of the portfolio evolution loop.

Fake executor/editor only: no Docker, network, or model. Three dated
snapshot tasks go through ``run_daily`` and one sequential ``run_evolution``;
lessons reflected after a task may only flow to later (higher-ordinal) tasks.
"""

import json
from datetime import date, timedelta
from pathlib import Path

from harness_clone.contracts import (
    Candidate,
    EditProposal,
    ExecutionRecord,
    ExperimentConfig,
    HarnessPatch,
    MarketSummary,
    PortfolioPosition,
    PortfolioSnapshot,
)
from harness_clone.editors import Reflection
from harness_clone.portfolio_ops import run_daily, run_evolution
from harness_clone.portfolio_snapshot import build_snapshot_task
from harness_clone.storage import RunStore

REPO = Path(__file__).resolve().parents[1]
DAYS = [date(2026, 10, 19) + timedelta(days=i) for i in range(3)]


def _portfolio():
    return PortfolioSnapshot(
        fetched_at="2026-10-19T08:00:00Z",
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


def _recommendations():
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
    def __init__(self):
        self.calls = []

    def execute(self, task, bundle_digest, execution_id):
        self.calls.append((task.id, bundle_digest, execution_id))
        return ExecutionRecord(
            execution_id=execution_id,
            task_id=task.id,
            bundle_digest=bundle_digest,
            delivered=True,
            artifacts={
                "recommendations.json": _recommendations(),
                "report.md": f"# Report\n\nBundle digest: {bundle_digest}\nResearch only.\n",
                "answer.txt": "verified output",
            },
            trace=[{"id": "0", "type": "tool", "content": "checked source"}],
        )


class FakeEditor:
    """Records the library task IDs visible to each task; reflects a lesson."""

    def __init__(self, task_id, seen):
        self.task_id = task_id
        self.seen = seen
        self.count = 0

    def propose(self, scope, parent_digest, evidence):
        self.seen.setdefault(self.task_id, set()).update(
            entry["task_id"] for entry in evidence.library
        )
        self.count += 1
        return EditProposal(
            patch=HarnessPatch(
                parent_digest=parent_digest,
                upserts={"memory/lesson.md": f"Check snapshot evidence first ({self.count})"},
            ),
            rationale="Snapshot was inspected",
            evidence_refs=[evidence.records[0].execution_id + ":trace:0"],
        )

    def choose(self, task, records):
        return records[0].execution_id, "first valid output"

    def reflect(self, evidence, result):
        self.seen.setdefault(self.task_id, set()).update(
            entry["task_id"] for entry in evidence.library
        )
        return Reflection(
            cue="portfolio snapshot",
            lesson="inspect frozen snapshot evidence before recommending",
            evidence_refs=[evidence.records[0].execution_id + ":trace:0"],
        )


def run_three_day_evolution(tmp_path):
    """Run daily + one sequential evolution over three dated tasks.

    Returns ``(results, tasks, executor, seen_library_by_task_id)`` where
    ``seen_library_by_task_id`` maps each task ID to the set of task IDs whose
    reflected lessons were visible in evidence while that task was processed.
    """
    store = RunStore(tmp_path / "run")
    base_digest = store.publish_bundle(REPO / "examples" / "portfolio-harness")
    summaries = {"AAPL": _summary("AAPL", "equity", 120.0), "SPY": _summary("SPY", "etf", 500.0)}
    candidates = [Candidate(ticker="MSFT", asset_class="equity", score=9.5, metrics={})]
    tasks = [
        build_snapshot_task(
            tmp_path / "snapshots", day, _portfolio(), summaries, candidates, 600, []
        )
        for day in DAYS
    ]
    executor = FakeExecutor()
    for task in tasks:
        record = run_daily(store, executor, task, base_digest)
        assert record.delivered is True
    seen: dict[str, set[str]] = {}
    config = ExperimentConfig(mode="sequential", k=1)
    results = run_evolution(
        tasks,
        config,
        store,
        executor,
        lambda task: FakeEditor(task.id, seen),
        base_digest,
    )
    return results, tasks, executor, seen


def evidence_task_ids(seen, task):
    return seen.get(task.id, set())


def test_forward_lessons_only_flow_forward(tmp_path):
    results, tasks, executor, seen = run_three_day_evolution(tmp_path)
    assert [result.task_id for result in results] == [task.id for task in tasks]
    assert all(result.selection.chosen_execution_id for result in results)
    # run_daily executed exactly once per dated task before evolution.
    daily_calls = [call for call in executor.calls if call[2].endswith("/daily")]
    assert len(daily_calls) == 3

    day1, day2, day3 = tasks
    assert evidence_task_ids(seen, day1) == set()
    assert evidence_task_ids(seen, day2) == {day1.id}
    day3_evidence = evidence_task_ids(seen, day3)
    assert day1.id in day3_evidence and day2.id in day3_evidence
    assert day3.id not in day3_evidence
