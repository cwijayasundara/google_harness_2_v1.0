"""Exercise the real agent graph with deterministic provider responses."""

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from fakes import ScriptedModel
from harness_clone.contracts import ExperimentConfig, TaskSpec
from harness_clone.editors import Choice, DeepAgentEditor
from harness_clone.storage import RunStore


def editor_with_model(tmp_path, monkeypatch, replies):
    model = ScriptedModel(replies=replies)
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-not-a-key")
    monkeypatch.setattr("harness_clone.editors.make_model", lambda *a, **k: model)
    task = TaskSpec(
        id="t", domain="test", prompt="choose", source_root=tmp_path, output_paths=["answer.json"]
    )
    editor = DeepAgentEditor(task, ExperimentConfig(max_usd="1"), RunStore(tmp_path / "run"))
    editor.set_stage("t/select", 0, "", [])
    return editor, model


def test_selector_recovers_from_truncated_response_without_a_choice(tmp_path, monkeypatch):
    editor, model = editor_with_model(
        tmp_path,
        monkeypatch,
        [
            AIMessage(content="", response_metadata={"finish_reason": "length"}),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "Choice",
                        "args": {"candidate": "candidate-1", "reason": "matches inputs"},
                        "id": "choice-1",
                        "type": "tool_call",
                    }
                ],
            ),
        ],
    )
    result = editor._run("SELECT", {"candidates": {"candidate-1": {}}}, Choice)
    assert result == Choice(candidate="candidate-1", reason="matches inputs")
    assert len(model.seen) == 2
    diagnostic = editor.store.get("editor-response", "t/select/attempt-0/response-0")
    assert diagnostic["finish_reason"] == "length"
    assert diagnostic["structured"] is False


def test_missing_structured_response_has_bounded_retry_and_diagnostic(tmp_path, monkeypatch):
    editor, model = editor_with_model(tmp_path, monkeypatch, [AIMessage(content="No choice")])
    with pytest.raises(RuntimeError, match="SELECT.*Choice.*2 attempts"):
        editor._run("SELECT", {}, Choice)
    assert len(model.seen) == 2
    assert editor.store.get("editor-response", "t/select/attempt-0/response-1") is not None


def test_reflection_bounds_repeated_traces_and_preserves_evidence_ids(tmp_path, monkeypatch):
    import json

    from harness_clone.contracts import ExecutionRecord, SelectionRecord, TaskResult
    from harness_clone.evidence import build_evidence, validate_refs

    editor, _ = editor_with_model(tmp_path, monkeypatch, [])
    records = [
        ExecutionRecord(
            execution_id=f"exec-{i}",
            task_id="t",
            bundle_digest="0" * 64,
            artifacts={"answer.json": "X" * 30000},
            trace=[{"id": j, "content": "Y" * 10000} for j in range(30)],
        )
        for i in range(4)
    ]
    evidence = build_evidence(editor.task, records)
    result = TaskResult(
        task_id="t",
        executions={r.execution_id: r for r in records},
        selection=SelectionRecord(chosen_execution_id="exec-3"),
    )
    captured = {}

    def capture(role, context, schema):
        captured.update(context)

    monkeypatch.setattr(editor, "_run", capture)
    editor.reflect(evidence, result)
    assert len(json.dumps(captured).encode()) < 32000
    visible = captured["evidence"]["records"]
    assert [r["execution_id"] for r in visible] == ["exec-0", "exec-3"]
    for record in visible:
        validate_refs(
            [f"{record['execution_id']}:trace:{row['id']}" for row in record["trace"]], evidence
        )
    assert "excerpt" in captured["evidence"]["note"].lower()
    assert captured["valid_evidence_refs"]
    for ref in captured["valid_evidence_refs"]:
        validate_refs([ref], evidence)


def test_reflection_repairs_refs_that_are_not_evidence_ids(tmp_path, monkeypatch):
    from harness_clone.contracts import ExecutionRecord, SelectionRecord, TaskResult
    from harness_clone.evidence import build_evidence

    record = ExecutionRecord(
        execution_id="exec-1",
        task_id="t",
        bundle_digest="0" * 64,
        artifacts={"answer.json": "ok"},
        trace=[{"id": "0", "content": "checked"}],
    )
    valid = "exec-1:trace:0"
    editor, model = editor_with_model(
        tmp_path,
        monkeypatch,
        [
            AIMessage(
                content="",
                response_metadata={"finish_reason": "tool_calls"},
                tool_calls=[
                    {
                        "name": "Reflection",
                        "id": "bad",
                        "type": "tool_call",
                        "args": {
                            "cue": "stage errors",
                            "lesson": "read the record",
                            "evidence_refs": ["stages[0] status=fallback"],
                            "confidence": "observational",
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                response_metadata={"finish_reason": "tool_calls"},
                tool_calls=[
                    {
                        "name": "Reflection",
                        "id": "good",
                        "type": "tool_call",
                        "args": {
                            "cue": "stage errors",
                            "lesson": "read the record",
                            "evidence_refs": [valid],
                            "confidence": "observational",
                        },
                    }
                ],
            ),
        ],
    )
    evidence = build_evidence(editor.task, [record])
    result = TaskResult(
        task_id="t",
        executions={record.execution_id: record},
        selection=SelectionRecord(chosen_execution_id=record.execution_id),
    )
    draft = editor.reflect(evidence, result)
    assert draft.evidence_refs == [valid]
    assert len(model.seen) == 2


def test_text_edit_context_lists_exact_citeable_refs(tmp_path, monkeypatch):
    from harness_clone.contracts import EvidenceSnapshot, ExecutionRecord

    editor, _ = editor_with_model(tmp_path, monkeypatch, [])
    (tmp_path / "src").mkdir()
    editor.task = editor.task.model_copy(update={"source_root": tmp_path / "src"})
    digest = editor.store.publish_bundle(Path(__file__).parents[1] / "examples" / "base-harness")
    record = ExecutionRecord(
        execution_id="t/base1",
        task_id="t",
        bundle_digest=digest,
        status="completed",
        delivered=True,
        trace=[{"id": "0", "type": "human", "content": "go"}],
        artifacts={"answer.json": "{}"},
    )
    evidence = EvidenceSnapshot(digest="d", task_id="t", records=[record])
    seen = {}

    def capture(role, context, schema):
        seen.update(context)
        raise RuntimeError("stop")

    monkeypatch.setattr(editor, "_run", capture)
    with pytest.raises(RuntimeError):
        editor.propose("INV", digest, evidence)
    assert seen["valid_evidence_refs"] == ["t/base1:trace:0", "t/base1:artifact:answer.json"]
