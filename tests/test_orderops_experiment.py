import csv
import io
import json
from pathlib import Path

import pytest

from harness_clone.contracts import EditProposal, ExecutionRecord, ExperimentConfig, HarnessPatch
from harness_clone.editors import Reflection
from harness_clone.orderops.cases import generate_suite
from harness_clone.orderops.experiment import load_suite, run_experiment
from harness_clone.orderops.score import evaluate_experiment
from harness_clone.storage import RunStore

BASE = Path(__file__).parents[1] / "examples/base-harness"


class Executor:
    def __init__(self, store, fail_edits=False):
        self.store, self.calls, self.fail_edits = store, [], fail_edits

    def execute(self, task, bundle_digest, execution_id):
        self.calls.append((task.id, bundle_digest, execution_id))
        rows = list(csv.DictReader(io.StringIO((task.source_root / "orders.csv").read_text())))
        artifacts = {
            "findings.json": json.dumps(
                [
                    dict(
                        order_id=r["order_id"],
                        issue="none",
                        amount_pence=0,
                        evidence_refs=[f"orders.csv:{i}"],
                    )
                    for i, r in enumerate(rows, 2)
                ]
            ),
            "proposed_actions.json": json.dumps(
                [dict(order_id=r["order_id"], action="no_action", amount_pence=0) for r in rows]
            ),
            "report.md": "Investigated all orders.",
        }
        failed = (
            self.fail_edits
            and (self.store.bundle_path(bundle_digest) / "memory/learned.md").exists()
        )
        return ExecutionRecord(
            execution_id=execution_id,
            task_id=task.id,
            bundle_digest=bundle_digest,
            delivered=not failed,
            artifacts=artifacts,
            trace=[{"id": 0, "content": "inspected"}],
        )


class Editor:
    def __init__(self, task, observed):
        self.task, self.observed = task, observed

    def propose(self, scope, parent_digest, evidence):
        self.observed.append((self.task.id, scope, [e["task_id"] for e in evidence.library]))
        return EditProposal(
            patch=HarnessPatch(
                parent_digest=parent_digest,
                upserts={"memory/learned.md": f"Verify evidence ({self.task.id})."},
            ),
            rationale="verify evidence",
            evidence_refs=[evidence.records[0].execution_id + ":trace:0"],
        )

    def choose(self, task, records):
        return records[-1].execution_id, "prefer revised output"

    def reflect(self, evidence, result):
        return Reflection(
            cue="reconciliation",
            lesson="check event state transitions",
            evidence_refs=[evidence.records[0].execution_id + ":trace:0"],
        )


def setup_run(tmp_path, fail_edits=False):
    generate_suite(tmp_path / "tasks", tmp_path / "private", dev_count=2, holdout_count=1)
    tasks = load_suite(tmp_path / "tasks")
    store = RunStore(tmp_path / "run")
    base = store.publish_bundle(BASE)
    executor = Executor(store, fail_edits)
    seen = []

    def factory(task):
        return Editor(task, seen)

    return tasks, store, base, executor, seen, factory


def test_persistent_carries_bundle_and_lessons_while_local_resets(tmp_path):
    tasks, store, base, executor, seen, factory = setup_run(tmp_path)
    result = run_experiment(
        tasks,
        ExperimentConfig(profile="practical", mode="sequential"),
        store,
        executor,
        factory,
        base,
    )
    persistent = result["arms"]["persistent"]["development"]
    assert persistent[0]["start_digest"] == base
    assert persistent[1]["start_digest"] == persistent[0]["selected_digest"] != base
    local = result["arms"]["local"]["development"]
    assert all(row["start_digest"] == base for row in local)
    assert all(not lessons for task_id, _, lessons in seen if task_id.startswith("local-"))
    assert any(lessons for task_id, _, lessons in seen if task_id == "persistent-dev-001")
    assert all(not task_id.endswith("holdout-000") for task_id, _, _ in seen)
    for arm in result["arms"].values():
        assert arm["heldout"][0]["start_digest"] == arm["final_digest"]
    before = len(executor.calls)
    assert (
        run_experiment(
            tasks,
            ExperimentConfig(profile="practical", mode="sequential"),
            store,
            executor,
            factory,
            base,
        )
        == result
    )
    assert len(executor.calls) == before


def test_failed_compositions_preserve_base(tmp_path):
    tasks, store, base, executor, _, factory = setup_run(tmp_path, fail_edits=True)
    result = run_experiment(
        tasks,
        ExperimentConfig(profile="practical", mode="sequential"),
        store,
        executor,
        factory,
        base,
        arms=("persistent",),
    )
    assert result["arms"]["persistent"]["final_digest"] == base


def test_changed_sources_reject_resume_without_replacing_identity(tmp_path):
    tasks, store, base, executor, _, factory = setup_run(tmp_path)
    config = ExperimentConfig(profile="practical", mode="sequential")
    run_experiment(tasks, config, store, executor, factory, base, arms=("frozen",))
    before = store.get("orderops", "admission")
    (tasks["dev"][0].source_root / "orders.csv").write_text("tampered")
    with pytest.raises(ValueError, match="changed"):
        run_experiment(tasks, config, store, executor, factory, base, arms=("frozen",))
    assert store.get("orderops", "admission") == before


def test_offline_report_counts_missing_holdout_and_refuses_online_output(tmp_path):
    tasks, store, base, executor, _, factory = setup_run(tmp_path)
    run_experiment(
        tasks,
        ExperimentConfig(profile="practical", mode="sequential"),
        store,
        executor,
        factory,
        base,
        arms=("frozen",),
    )
    answers = tmp_path / "private/answers.json"
    with pytest.raises(ValueError, match="outside"):
        evaluate_experiment(store.root, answers, store.root / "score.json")
    summary = store.get("orderops", "summary")
    summary["arms"]["frozen"]["heldout"] = []
    store.put("orderops", "summary", summary)
    report = evaluate_experiment(store.root, answers, tmp_path / "report.json")
    assert report["arms"]["frozen"]["heldout"]["cases"] == 1
    assert report["arms"]["frozen"]["heldout"]["delivery_coverage"] == 0
    assert report["arms"]["frozen"]["heldout"]["success_rate"] == 0


def test_run_refuses_source_directory_inside_run(tmp_path):
    tasks, store, base, executor, _, factory = setup_run(tmp_path)
    tasks["dev"][0] = tasks["dev"][0].model_copy(update={"source_root": store.root})
    with pytest.raises(ValueError, match="overlap"):
        run_experiment(
            tasks,
            ExperimentConfig(profile="practical", mode="sequential"),
            store,
            executor,
            factory,
            base,
        )


def test_no_baseline_delivery_counts_failed_case_instead_of_aborting(tmp_path):
    tasks, store, base, executor, _, factory = setup_run(tmp_path)
    original = executor.execute

    def fail_baseline(task, digest, execution_id):
        return original(task, digest, execution_id).model_copy(update={"delivered": False})

    executor.execute = fail_baseline
    report = run_experiment(
        tasks,
        ExperimentConfig(profile="practical", mode="sequential"),
        store,
        executor,
        factory,
        base,
        arms=("persistent",),
    )
    assert report["arms"]["persistent"]["final_digest"] == base
    assert all(r["execution"] is None for r in report["arms"]["persistent"]["development"])


def test_retry_control_matches_actual_local_attempts_after_failed_baseline(tmp_path):
    tasks, store, base, executor, _, factory = setup_run(tmp_path)
    original = executor.execute

    def fail(task, digest, execution_id):
        return original(task, digest, execution_id).model_copy(update={"delivered": False})

    executor.execute = fail
    report = run_experiment(
        tasks,
        ExperimentConfig(profile="practical", mode="sequential"),
        store,
        executor,
        factory,
        base,
        arms=("retries", "local"),
    )
    local = report["arms"]["local"]["development"]
    retries = report["arms"]["retries"]["development"]
    assert [r["execution_count"] for r in local] == [1, 1]
    assert [r["execution_count"] for r in retries] == [1, 1]


def test_answer_key_cannot_be_overwritten_or_used_for_different_inputs(tmp_path):
    tasks, store, base, executor, _, factory = setup_run(tmp_path)
    run_experiment(
        tasks,
        ExperimentConfig(profile="practical", mode="sequential"),
        store,
        executor,
        factory,
        base,
        arms=("frozen",),
    )
    answers = tmp_path / "private/answers.json"
    with pytest.raises(ValueError, match="answer"):
        evaluate_experiment(store.root, answers, answers)
    data = json.loads(answers.read_text())
    data["cases"]["dev-000"]["source_digest"] = "wrong"
    answers.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="digest"):
        evaluate_experiment(store.root, answers, tmp_path / "report.json")


def test_evaluation_cannot_write_hidden_results_over_public_task_metadata(tmp_path):
    tasks, store, base, executor, _, factory = setup_run(tmp_path)
    run_experiment(
        tasks,
        ExperimentConfig(profile="practical", mode="sequential"),
        store,
        executor,
        factory,
        base,
        arms=("frozen",),
    )
    for output in [tmp_path / "tasks/dev-000/task.json", tmp_path / "tasks/suite.json"]:
        before = output.read_text()
        with pytest.raises(ValueError, match="outside"):
            evaluate_experiment(store.root, tmp_path / "private/answers.json", output)
        assert output.read_text() == before


def test_invalid_delivery_still_reports_parseable_false_actions(tmp_path):
    tasks, store, base, executor, _, factory = setup_run(tmp_path)
    run_experiment(
        tasks,
        ExperimentConfig(profile="practical", mode="sequential"),
        store,
        executor,
        factory,
        base,
        arms=("frozen",),
    )
    summary = store.get("orderops", "summary")
    execution = summary["arms"]["frozen"]["heldout"][0]["execution"]
    execution["delivered"] = False
    actions = json.loads(execution["artifacts"]["proposed_actions.json"])
    for action in actions:
        action.update(action="refund", amount_pence=1)
    execution["artifacts"]["proposed_actions.json"] = json.dumps(actions)
    store.put("orderops", "summary", summary)
    report = evaluate_experiment(
        store.root, tmp_path / "private/answers.json", tmp_path / "evaluation.json"
    )
    heldout = report["arms"]["frozen"]["heldout"]
    assert heldout["success_rate"] == 0
    assert heldout["false_active_actions"] == 3
