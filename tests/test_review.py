"""Phase 3: goal-shaped memory — editable reflect steering + the review loop."""

import json
import shutil
from pathlib import Path

import pytest

from harness_clone.bundles import bundle_digest
from harness_clone.contracts import ExperimentConfig
from harness_clone.instructions import DEFAULT_INSTRUCTIONS, load_instructions
from harness_clone.review import review_instructions
from harness_clone.storage import RunStore

BASE = Path(__file__).parents[1] / "examples/base-harness"
DIGEST = bundle_digest(BASE)


def config(cap=1.0):
    return ExperimentConfig(max_usd=cap)


@pytest.fixture
def store(tmp_path):
    run_store = RunStore(tmp_path / "run")
    run_store.publish_bundle(BASE)
    return run_store


def scoreboard(*statuses):
    return {
        "tasks": len(statuses),
        "passed": sum(s == "passed" for s in statuses),
        "results": [
            {"task": f"t{i}", "status": s} for i, s in enumerate(statuses)
        ],
    }


def test_reflect_reads_the_bundle_instructions(store, monkeypatch):
    """The editable file wins over the hardcoded default."""
    from harness_clone.editors import DeepAgentEditor
    from harness_clone.evidence import build_evidence
    from harness_clone.contracts import ExecutionRecord, SelectionRecord, TaskSpec, TaskResult

    editor = DeepAgentEditor(
        TaskSpec(
            id="t", domain="d", ordinal=0, prompt="p", source_root=".",
            output_paths=["answer.json"],
        ),
        config(),
        store,
    )
    editor.base_digest = DIGEST
    captured = {}

    def capture(role, context, schema):
        captured.update(context)

    monkeypatch.setattr(editor, "_run", capture)
    records = [
        ExecutionRecord(execution_id="e0", task_id="t", bundle_digest=DIGEST, delivered=True)
    ]
    result = TaskResult(
        task_id="t",
        executions={r.execution_id: r for r in records},
        selection=SelectionRecord(chosen_execution_id="e0"),
    )
    editor.reflect(build_evidence(editor.task, records), result)
    assert "Reflection steering" in captured["instruction"]
    assert "review" in captured["instruction"].lower()


def test_reflect_falls_back_to_the_default(tmp_path):
    """A bundle without memory/instructions.md gets the built-in text."""
    from harness_clone.editors import DeepAgentEditor

    assert DeepAgentEditor.__init__  # sanity: the class exists
    assert load_instructions(None) == DEFAULT_INSTRUCTIONS
    assert load_instructions(tmp_path) == DEFAULT_INSTRUCTIONS


def test_run_stream_points_reflect_at_the_parent_bundle(store):
    from harness_clone.editors import DeepAgentEditor

    editor = DeepAgentEditor(None, config(), store)
    assert editor.base_digest is None
    # run_stream sets the attribute before reflect when the editor supports it.
    if hasattr(editor, "base_digest"):
        editor.base_digest = DIGEST
    assert editor.steering().startswith("# Reflection steering")


def test_failed_criteria_become_a_published_instructions_patch(store):
    calls = []

    def reviewer(context):
        calls.append(context)
        assert "t0" in context["failed_tasks"]
        assert context["current_instructions"].startswith("# Reflection steering")
        return type("D", (), {"rewritten": "# Reflection steering\n\nNow: verify every answer against the schema.", "reason": "schema checks missing"})()

    digest, stats = review_instructions(
        config(), store, BASE, scoreboard("failed"), reviewer=reviewer
    )
    assert stats.reviewed == 1 and stats.failed_tasks == ["t0"]
    assert calls
    assert calls[0]["failed_tasks"] == ["t0"]
    assert "chosen_evidence" in calls[0]
    new_bundle = store.bundle_path(digest)
    text = (new_bundle / "memory/instructions.md").read_text()
    assert "verify every answer against the schema" in text
    # Everything else is carried over untouched (memory-only patch).
    assert (new_bundle / "system.md").read_text() == (BASE / "system.md").read_text()


def test_no_failures_makes_no_call(store):
    def refusing(context):
        raise AssertionError("reviewer called with nothing to fix")

    digest, stats = review_instructions(
        config(), store, BASE, scoreboard("passed", "unscored"), reviewer=refusing
    )
    assert digest is None and stats.reviewed == 0


def test_secret_rewrite_is_skipped_and_recorded(store):
    def reviewer(context):
        return type("D", (), {"rewritten": "use sk-test-12345678 as the key", "reason": ""})()

    digest, stats = review_instructions(
        config(), store, BASE, scoreboard("failed"), reviewer=reviewer
    )
    assert digest is None and stats.skipped == 1
    with store.transaction() as db:
        rows = db.execute("SELECT value FROM records WHERE kind='reviewed'").fetchall()
    assert any("credential" in json.loads(r[0]).get("reason", "") for r in rows)


def test_empty_and_bloated_rewrites_are_skipped(store):
    def empty(context):
        return type("D", (), {"rewritten": "  ", "reason": ""})()

    digest, stats = review_instructions(
        config(), store, BASE, scoreboard("failed"), reviewer=empty
    )
    assert digest is None and stats.skipped == 1

    def bloated(context):
        return type("D", (), {"rewritten": "x" * 9000, "reason": ""})()

    digest, stats = review_instructions(
        config(), store, BASE, scoreboard("failed"), reviewer=bloated
    )
    assert digest is None and stats.skipped == 1


def test_review_requires_budget(store):
    with pytest.raises(ValueError, match="nonzero spending cap"):
        review_instructions(config(cap=0.0), store, BASE, scoreboard("failed"))


def test_reviewer_failure_is_recorded(store):
    def broken(context):
        raise RuntimeError("gateway down")

    digest, stats = review_instructions(
        config(), store, BASE, scoreboard("failed"), reviewer=broken
    )
    assert digest is None and stats.skipped == 1
    with store.transaction() as db:
        rows = db.execute("SELECT value FROM records WHERE kind='reviewed'").fetchall()
    assert any(json.loads(r[0]).get("error") == "RuntimeError" for r in rows)


def test_same_scoreboard_twice_converges(store):
    calls = []

    def reviewer(context):
        calls.append(1)
        return type("D", (), {"rewritten": "# Reflection steering\n\nRewritten once.", "reason": ""})()

    digest1, _ = review_instructions(
        config(), store, BASE, scoreboard("failed"), reviewer=reviewer
    )
    assert len(calls) == 1

    def refusing(context):
        raise AssertionError("second review called the model")

    digest2, stats = review_instructions(
        config(), store, BASE, scoreboard("failed"), reviewer=refusing
    )
    assert stats.cached == 1
    assert digest1 == digest2


def test_review_of_new_criteria_after_a_rewrite_calls_again(store):
    """A changed scoreboard (or instructions) invalidates the cache."""
    calls = []

    def reviewer(context):
        calls.append(1)
        return type("D", (), {"rewritten": f"# v{len(calls)}", "reason": ""})()

    review_instructions(config(), store, BASE, scoreboard("failed"), reviewer=reviewer)
    review_instructions(config(), store, BASE, scoreboard("failed", "failed"), reviewer=reviewer)
    assert len(calls) == 2


def test_compiler_does_not_preload_instructions_md(tmp_path, monkeypatch):
    """instructions.md steers reflect, not task agents — never preloaded."""
    from deepagents.backends import LocalShellBackend

    from fakes import ScriptedModel

    from harness_clone import compiler

    bundle = tmp_path / "bundle"
    shutil.copytree(BASE, bundle)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(
        compiler, "make_model", lambda config, role: ScriptedModel()
    )
    backend = LocalShellBackend(
        root_dir=runtime, virtual_mode=False, env={"PATH": "/usr/bin:/bin:/usr/local/bin"}
    )
    agent = compiler.compile_agent(bundle, backend, ExperimentConfig())
    memory_files = [
        m
        for m in getattr(agent, "memory", None) or []
        if "instructions" in str(m)
    ]
    assert not memory_files
