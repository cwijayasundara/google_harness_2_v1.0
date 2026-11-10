"""Phase 4: the metered rubric judge."""

import json

import pytest
from pathlib import Path

from harness_clone.bundles import bundle_digest
from harness_clone.contracts import (
    ExecutionRecord,
    ExperimentConfig,
    SelectionRecord,
    TaskResult,
)
from harness_clone.evaluate import evaluate
from harness_clone.judge import RubricDecision, RubricGrade, judge, judge_to
from harness_clone.storage import RunStore

BASE = Path(__file__).parents[1] / "examples/base-harness"
DIGEST = bundle_digest(BASE)
CRITERIA = ["says the east side", "cites the checkpoint row"]


def config(cap=1.0):
    return ExperimentConfig(max_usd=cap)


def chosen_record(artifacts):
    return ExecutionRecord(
        execution_id="t1/identity/agg1",
        task_id="t1",
        bundle_digest=DIGEST,
        delivered=True,
        artifacts=artifacts,
        trace=[{"id": "t0", "content": "done"}],
    )


def result_for(chosen):
    return TaskResult(
        task_id="t1",
        executions={chosen.execution_id: chosen},
        selection=SelectionRecord(chosen_execution_id=chosen.execution_id),
    )


@pytest.fixture
def store(tmp_path):
    run_store = RunStore(tmp_path / "run")
    run_store.publish_bundle(BASE)
    run_store.put(
        "runtime",
        "results",
        [result_for(chosen_record({"answer.json": "the checkpoint is on the east side"})).model_dump(mode="json")],
    )
    return run_store


def grader(passed=True, criteria=None, drop=None):
    criteria = criteria if criteria is not None else CRITERIA
    grades = [
        RubricGrade(criterion=c, passed=passed, reason="because")
        for c in criteria
        if c != drop
    ]

    def fake(context):
        assert context["criteria"] == CRITERIA
        assert "east side" in context["output"]["artifacts"]["answer.json"]
        return RubricDecision(grades=grades)

    return fake


def expect(artifact=None, rubric=None):
    entry = {}
    if artifact:
        entry.update(artifact)
    if rubric:
        entry["rubric"] = rubric
    return {"t1": entry}


def test_rubric_grading_produces_the_scoreboard(store):
    report, stats = judge(store, expect(rubric=CRITERIA), config(), grader=grader())
    assert stats.graded == 1 and stats.rubric_tasks == ["t1"]
    row = report["results"][0]
    assert row["status"] == "passed"
    assert [g["criterion"] for g in row["grades"]] == CRITERIA


def test_failing_criterion_fails_the_task(store):
    report, _ = judge(
        store, expect(rubric=CRITERIA), config(), grader=grader(passed=False)
    )
    assert report["results"][0]["status"] == "failed"


def test_tasks_without_rubric_make_no_call(store):
    calls = []

    def spy(context):
        calls.append(1)
        return RubricDecision(grades=[])

    report, stats = judge(
        store, expect(artifact={"answer.json": {"side": "east"}}), config(), grader=spy
    )
    assert calls == [] and stats.graded == 0
    assert report["tasks"] == 0


def test_missing_output_is_unscored(tmp_path):
    run_store = RunStore(tmp_path / "empty")
    run_store.publish_bundle(BASE)
    report, stats = judge(
        run_store, expect(rubric=CRITERIA), config(), grader=grader()
    )
    assert report["results"][0]["status"] == "unscored"
    assert stats.skipped == 1


def test_grader_failure_is_recorded(store):
    def broken(context):
        raise RuntimeError("gateway down")

    report, stats = judge(store, expect(rubric=CRITERIA), config(), grader=broken)
    assert report["results"][0]["status"] == "unscored"
    assert stats.skipped == 1
    assert store.get("judged", "t1:" + _key(store, "t1"))["error"] == "RuntimeError"


def _key(store, task_id):
    with store.transaction() as db:
        rows = db.execute("SELECT id FROM records WHERE kind='judged'").fetchall()
    return next(row[0].split(":", 1)[1] for row in rows if row[0].startswith(task_id))


def test_incomplete_grades_are_unscored(store):
    report, stats = judge(
        store, expect(rubric=CRITERIA), config(), grader=grader(drop=CRITERIA[1])
    )
    assert report["results"][0]["status"] == "unscored"
    assert stats.skipped == 1


def test_judge_requires_budget(store):
    with pytest.raises(ValueError, match="nonzero spending cap"):
        judge(store, expect(rubric=CRITERIA), config(cap=0.0))


def test_same_output_and_rubric_judge_once(store):
    calls = []

    def counting(context):
        calls.append(1)
        return grader()(context)

    first, _ = judge(store, expect(rubric=CRITERIA), config(), grader=counting)
    assert len(calls) == 1

    def refusing(context):
        raise AssertionError("second judge called the model")

    second, stats = judge(store, expect(rubric=CRITERIA), config(), grader=refusing)
    assert stats.cached == 1 and calls == [1]
    assert first == second


def test_merge_semantics(store):
    evaluation = {"results": [{"task": "t1", "status": "failed"}]}
    report, _ = judge(
        store, expect(rubric=CRITERIA), config(), evaluation=evaluation, grader=grader()
    )
    row = report["results"][0]
    assert row["status"] == "failed"  # artifact failed, rubric passed
    assert row["artifact_status"] == "failed" and row["rubric_status"] == "passed"

    evaluation["results"][0]["status"] = "passed"
    report, _ = judge(
        store, expect(rubric=CRITERIA), config(), evaluation=evaluation, grader=grader()
    )
    assert report["results"][0]["status"] == "passed"

    evaluation["results"][0]["status"] = "unscored"
    report, _ = judge(
        store, expect(rubric=CRITERIA), config(), evaluation=evaluation, grader=grader()
    )
    assert report["results"][0]["status"] == "unscored"


def test_report_never_lands_inside_the_run_dir(store, tmp_path):
    with pytest.raises(ValueError, match="outside the online run directory"):
        judge_to(
            store, expect(rubric=CRITERIA), config(), store.root / "scoreboard.json",
            grader=grader(),
        )


def test_evaluate_ignores_the_rubric_key(store, tmp_path):
    output = tmp_path / "scoreboard.json"
    report = evaluate(
        store,
        expect(artifact={"answer.json": "the checkpoint is on the east side"}, rubric=CRITERIA),
        output,
    )
    row = report["results"][0]
    assert row["status"] == "passed"  # artifact matched; rubric never touched here


def test_cli_judge_guards(tmp_path, capsys):
    from harness_clone.cli import main

    expectations = tmp_path / "exp.json"
    expectations.write_text(json.dumps(expect(rubric=CRITERIA)))
    rc = main(
        [
            "judge", "--run-dir", str(tmp_path / "nope"),
            "--expectations", str(expectations), "--output", str(tmp_path / "out.json"),
            "--max-usd", "0",
        ]
    )
    assert rc == 2
    assert "nonzero" in capsys.readouterr().err



