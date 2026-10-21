"""Explicit, replayable LangGraph scheduling for Harness² task recursion."""

import tempfile
from pathlib import Path
from typing import Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from .bundles import apply_patch, validate_bundle
from .contracts import (
    EditProposal,
    ExecutionRecord,
    ExperimentConfig,
    SelectionRecord,
    TaskResult,
    TaskSpec,
)
from .evidence import build_evidence, read_sources, validate_refs
from .storage import RunStore, fingerprint


class Executor(Protocol):
    def execute(self, task: TaskSpec, bundle_digest: str, execution_id: str) -> ExecutionRecord: ...


class Editor(Protocol):
    def propose(self, scope: str, parent_digest: str, evidence) -> EditProposal: ...
    def choose(self, task: TaskSpec, records: list[ExecutionRecord]) -> tuple[str, str]: ...


class GraphState(TypedDict):
    round: int
    current: str
    proposals: list[str]
    compositions: list[str]


def run_task(
    task: TaskSpec,
    config: ExperimentConfig,
    store: RunStore,
    executor: Executor,
    editor: Editor,
    base_digest: str,
    library: list[dict] | None = None,
) -> TaskResult:
    store.create_experiment(config)
    validate_bundle(store.bundle_path(base_digest), config)
    identity = fingerprint(
        {
            "task": task.model_dump(mode="json"),
            "source": read_sources(task),
            "config": config.model_dump(mode="json"),
            "base": base_digest,
            "library": library or [],
        }
    )
    prefix = f"{task.id}/{identity[:16]}"
    old = store.get("task", identity)
    if old:
        result = TaskResult.model_validate(old)
        for record in result.executions.values():
            if record.status == "completed":
                store.completed_execution(record.execution_id)
        return result
    result = TaskResult(task_id=task.id)
    trees = {"base": base_digest}

    def roll(label, digest):
        eid = f"{prefix}/{label}"
        record = store.completed_execution(eid)
        if record is None:
            record = executor.execute(task, digest, eid)
            if (
                record.execution_id != eid
                or record.bundle_digest != digest
                or record.task_id != task.id
            ):
                raise ValueError("executor returned mismatched execution identity")
            store.save_execution(record)
        result.executions[label] = record
        return record

    def edit(label, scope, parent):
        key = f"{prefix}/{label}"
        saved = store.get("edit", key)
        if saved:
            validate_bundle(store.bundle_path(saved["digest"]), config)
            trees[label] = saved["digest"]
            result.stages.append(saved)
            return
        evidence = build_evidence(task, list(result.executions.values()), library)
        error = ""
        status = "fallback"
        digest = parent
        rationale = ""
        for attempt in range(2):
            try:
                if hasattr(editor, "set_stage"):
                    editor.set_stage(key, attempt, error, result.stages)
                proposal = editor.propose(scope, parent, evidence)
                if proposal.patch.parent_digest != parent:
                    raise ValueError("proposal parent mismatch")
                if not proposal.keep_base and not proposal.evidence_refs:
                    raise ValueError("changed proposals need evidence references")
                validate_refs(proposal.evidence_refs, evidence)
                if proposal.keep_base and (proposal.patch.upserts or proposal.patch.deletions):
                    raise ValueError("KEEP_BASE cannot include edits")
                with tempfile.TemporaryDirectory() as temp:
                    candidate = Path(temp) / "candidate"
                    apply_patch(store.bundle_path(parent), proposal.patch, candidate)
                    validate_bundle(candidate, config)
                    digest = store.publish_bundle(candidate)
                rationale = proposal.rationale
                status = "keep_base" if digest == parent else "accepted"
                break
            except (ValueError, OSError, RuntimeError) as exc:
                error = f"{type(exc).__name__}: {str(exc)[:500]}"
        saved = {
            "label": label,
            "scope": scope,
            "parent": parent,
            "digest": digest,
            "status": status,
            "attempts": attempt + 1,
            "error": error if status == "fallback" else "",
            "rationale": rationale,
        }
        store.put("edit", key, saved)
        trees[label] = digest
        result.stages.append(saved)

    def references(state):
        for i in range(1, config.k + 1):
            roll(f"base{i}", base_digest)
        if not any(r.delivered for r in result.executions.values()):
            raise RuntimeError("no baseline execution delivered; inspect execution errors")
        return {}

    def proposals(state):
        rounds = range(1, config.k + 1) if config.mode == "parallel" else [state["round"]]
        labels = []
        for i in rounds:
            for scope in ["INV", "DOM"]:
                label = f"{scope.lower()}{i}"
                edit(label, scope, state["current"])
                labels.append(label)
        return {"proposals": labels}

    def probes(state):
        for label in state["proposals"]:
            roll(label, trees[label])
        return {}

    def compose(state):
        labels = (
            [f"agg{i}" for i in range(1, config.k + 1)]
            if config.mode == "parallel"
            else [f"c{state['round']}"]
        )
        for label in labels:
            edit(label, "COMPOSE", state["current"])
        return {"compositions": labels}

    def finals(state):
        for label in state["compositions"]:
            roll(label, trees[label])
        return {"current": trees[state["compositions"][-1]], "round": state["round"] + 1}

    def select(state):
        labels = [
            f"agg{i}" if config.mode == "parallel" else f"c{i}" for i in range(1, config.k + 1)
        ]
        if config.profile == "practical":
            labels = [f"base{i}" for i in range(1, config.k + 1)] + labels
        records = [
            result.executions[label]
            for label in labels
            if result.executions[label].delivered and result.executions[label].status == "completed"
        ]
        if not records:
            result.errors.append("no eligible composed output delivered")
            return {}
        saved = store.get("selection", identity)
        if saved:
            result.selection = SelectionRecord.model_validate(saved)
        else:
            if len(records) == 1:
                chosen, reason = records[0].execution_id, "only eligible deliverable"
            else:
                chosen, reason = editor.choose(task, records)
            if chosen not in {r.execution_id for r in records}:
                raise ValueError("selector returned an ineligible output")
            result.selection = SelectionRecord(
                chosen_execution_id=chosen,
                eligible=[r.execution_id for r in records],
                reason=reason,
            )
            store.put("selection", identity, result.selection.model_dump(mode="json"))
        return {}

    graph = StateGraph(GraphState)
    for name, body in [
        ("references", references),
        ("proposals", proposals),
        ("probes", probes),
        ("compose", compose),
        ("finals", finals),
        ("select", select),
    ]:
        graph.add_node(name, body)
    graph.add_edge(START, "references")
    for start, end in [
        ("references", "proposals"),
        ("proposals", "probes"),
        ("probes", "compose"),
        ("compose", "finals"),
    ]:
        graph.add_edge(start, end)
    graph.add_conditional_edges(
        "finals",
        lambda state: (
            "proposals" if config.mode == "sequential" and state["round"] <= config.k else "select"
        ),
    )
    graph.add_edge("select", END)
    graph.compile().invoke(
        {"round": 1, "current": base_digest, "proposals": [], "compositions": []},
        {"recursion_limit": config.k * 6 + 10},
    )
    store.put("task", identity, result.model_dump(mode="json"))
    return result
