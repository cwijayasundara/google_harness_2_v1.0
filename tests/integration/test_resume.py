from pathlib import Path

import pytest

from harness_clone.contracts import ExperimentConfig, TaskSpec
from test_controller import FixtureEditor, FixtureExecutor


def test_interrupted_probe_resumes_without_repeating_completed_work(tmp_path):
    from harness_clone.controller import run_task
    from harness_clone.storage import RunStore

    store = RunStore(tmp_path / "runs")
    digest = store.publish_bundle(Path(__file__).parents[2] / "examples/base-harness")
    source = tmp_path / "source"
    source.mkdir()
    task = TaskSpec(
        id="a", domain="code", prompt="x", source_root=source, output_paths=["answer.txt"]
    )

    class Interrupted(FixtureExecutor):
        def execute(self, task, digest, eid):
            if eid.endswith("/dom1"):
                raise KeyboardInterrupt("simulated process death")
            return super().execute(task, digest, eid)

    editor = FixtureEditor()
    with pytest.raises(KeyboardInterrupt):
        run_task(task, ExperimentConfig(), store, Interrupted(), editor, digest)
    executor = FixtureExecutor()
    result = run_task(task, ExperimentConfig(), store, executor, editor, digest)
    assert len(executor.calls) == 2
    assert len(editor.calls) == 3
    assert result.selection.chosen_execution_id


def test_next_task_uses_only_earlier_experience_and_resume_is_idempotent(tmp_path):
    from harness_clone.experience import run_stream
    from harness_clone.storage import RunStore
    from harness_clone.editors import Reflection

    store = RunStore(tmp_path / "runs")
    digest = store.publish_bundle(Path(__file__).parents[2] / "examples/base-harness")
    source = tmp_path / "source"
    source.mkdir()
    tasks = [
        TaskSpec(
            id=t,
            domain="code",
            prompt="x",
            source_root=source,
            output_paths=["answer.txt"],
            ordinal=i,
        )
        for i, t in enumerate(["a", "b"])
    ]
    seen = []

    class Learning(FixtureEditor):
        def propose(self, scope, parent_digest, evidence):
            seen.append([e["task_id"] for e in evidence.library])
            return super().propose(scope, parent_digest, evidence)

        def reflect(self, evidence, result):
            return Reflection(
                cue="code",
                lesson="inspect sources",
                evidence_refs=[evidence.records[0].execution_id + ":trace:0"],
            )

    results = run_stream(
        tasks, ExperimentConfig(), store, FixtureExecutor(), lambda task: Learning(), digest
    )
    assert all(not e for e in seen[:3])
    assert all(e == ["a"] for e in seen[3:])
    assert len(results) == 2
    seen.clear()
    again = run_stream(
        tasks, ExperimentConfig(), store, FixtureExecutor(), lambda task: Learning(), digest
    )
    assert seen == []
    assert again == results
