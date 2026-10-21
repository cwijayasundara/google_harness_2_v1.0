"""Credential-free mechanism demonstration, not an LLM quality benchmark."""

import json
from pathlib import Path

from .contracts import EditProposal, ExecutionRecord, ExperimentConfig, HarnessPatch, TaskSpec
from .editors import Reflection
from .experience import run_stream
from .storage import RunStore


def default_bundle() -> Path:
    source = Path(__file__).resolve().parents[2] / "examples/base-harness"
    if source.exists():
        return source
    return Path(__file__).parent / "examples/base-harness"


class DemoExecutor:
    def execute(self, task, digest, execution_id):
        values = json.loads((task.source_root / "values.json").read_text())["values"]
        artifact = json.dumps({"count": len(values), "total": sum(values)}, sort_keys=True)
        return ExecutionRecord(
            execution_id=execution_id,
            task_id=task.id,
            bundle_digest=digest,
            delivered=True,
            artifacts={"answer.json": artifact},
            trace=[
                {
                    "id": "0",
                    "type": "tool",
                    "content": "Read values.json and computed count and total.",
                }
            ],
        )


class DemoEditor:
    def propose(self, scope, parent_digest, evidence):
        body = f"When processing numeric inputs, verify count and total against current sources. Scope: {scope}."
        return EditProposal(
            patch=HarnessPatch(parent_digest=parent_digest, upserts={"memory/lesson.md": body}),
            rationale="The execution explicitly read the supplied inputs and computed both requested values.",
            evidence_refs=[evidence.records[0].execution_id + ":trace:0"],
        )

    def choose(self, task, records):
        return records[
            0
        ].execution_id, "All demonstrated outputs satisfy the visible fixture contract."

    def reflect(self, evidence, result):
        return Reflection(
            cue="numeric summaries",
            lesson="Check count and total against current source values.",
            evidence_refs=[evidence.records[0].execution_id + ":trace:0"],
        )


def summarize(results, backend):
    return {
        "backend": backend,
        "tasks": len(results),
        "executions": sum(len(r.executions) for r in results),
        "editing_stages": sum(len(r.stages) for r in results),
        "selected": [r.selection.chosen_execution_id for r in results],
        "errors": [error for r in results for error in r.errors],
    }


def run_demo(root: Path, mode="parallel", k=1):
    root = root.resolve()
    store = RunStore(root)
    config = ExperimentConfig(mode=mode, k=k)
    tasks = []
    for i, (name, values) in enumerate([("demo-a", [2, 3, 5]), ("demo-b", [4, 6, 8])]):
        source = root / "inputs" / name
        source.mkdir(parents=True, exist_ok=True)
        file = source / "values.json"
        if not file.exists():
            file.write_text(json.dumps({"values": values}))
        tasks.append(
            TaskSpec(
                id=name,
                domain="numeric-summary",
                prompt="Read values.json and produce answer.json with count and total.",
                source_root=source,
                output_paths=["answer.json"],
                ordinal=i,
            )
        )
    digest = store.publish_bundle(default_bundle())
    store.put("runtime", "kind", {"backend": "deterministic-demo"})
    store.put("runtime", "tasks", [t.model_dump(mode="json") for t in tasks])
    results = run_stream(tasks, config, store, DemoExecutor(), lambda task: DemoEditor(), digest)
    store.put("runtime", "results", [r.model_dump(mode="json") for r in results])
    return summarize(results, "deterministic-demo")
