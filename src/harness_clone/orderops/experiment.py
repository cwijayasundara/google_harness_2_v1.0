"""Online experiments: no imports of private answers, generator, or grader."""

import json
import os
import re
import time
from pathlib import Path

from ..bundles import bundle_digest
from ..contracts import (
    EvidenceSnapshot,
    ExecutionRecord,
    ExperienceEntry,
    ExperimentConfig,
    TaskResult,
    TaskSpec,
)
from ..controller import run_task
from ..evidence import build_evidence, read_sources, validate_refs
from ..experience import ExperienceLibrary
from ..gateway import stage_group
from ..storage import BudgetLedger, fingerprint
from ..tasks import load_task
from .contracts import validate_artifacts

ARMS = ("frozen", "local", "persistent", "retries")


def load_suite(root: Path):
    root = Path(root).resolve()
    manifest = json.loads((root / "suite.json").read_text())
    if manifest["version"] != "orderops-v1":
        raise ValueError("unsupported OrderOps suite version")
    result, seen = {}, set()
    for split in ("dev", "holdout"):
        result[split] = []
        for task_id in manifest["splits"][split]:
            if not re.fullmatch(r"[\w-]+", task_id) or task_id in seen:
                raise ValueError("unsafe or duplicate task ID")
            seen.add(task_id)
            task = load_task(root / task_id / "task.json")
            if task.id != task_id or task.domain != "orderops":
                raise ValueError("task identity mismatch")
            result[split].append(task)
        if not result[split]:
            raise ValueError("empty task split")
        ordinals = [t.ordinal for t in result[split]]
        if len(set(ordinals)) != len(ordinals):
            raise ValueError("duplicate task ordinal")
        result[split].sort(key=lambda t: t.ordinal)
    return result


class TimedExecutor:
    def __init__(self, executor, store):
        self.executor, self.store = executor, store

    def execute(self, task, bundle_digest, execution_id):
        start = time.monotonic()
        record = self.executor.execute(task, bundle_digest, execution_id)
        self.store.put("orderops-time", execution_id, time.monotonic() - start)
        return record


def _valid(task, record):
    if record is None or not record.delivered or record.status != "completed":
        return False
    try:
        validate_artifacts(task, record.artifacts)
    except (ValueError, KeyError, TypeError):
        return False
    return True


def _snapshot(tasks_by_split, store):
    saved = store.get("orderops", "tasks")
    if saved:
        tasks = {s: [TaskSpec.model_validate(t) for t in group] for s, group in saved.items()}
        expected = store.get("orderops", "snapshot-digest")
        actual = fingerprint({s: [read_sources(t) for t in group] for s, group in tasks.items()})
        if actual != expected:
            raise ValueError("stored source snapshot changed")
        return tasks
    tasks = {}
    for split, group in tasks_by_split.items():
        tasks[split] = []
        for index, task in enumerate(group):
            root = store.root / "inputs" / split / str(index)
            root.mkdir(parents=True, exist_ok=True)
            for name, content in read_sources(task).items():
                dest = root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content)
            tasks[split].append(task.model_copy(update={"source_root": root}))
    store.put(
        "orderops",
        "snapshot-digest",
        fingerprint({s: [read_sources(t) for t in group] for s, group in tasks.items()}),
    )
    store.put(
        "orderops",
        "tasks",
        {s: [t.model_dump(mode="json") for t in group] for s, group in tasks.items()},
    )
    return tasks


def _direct(task, digest, execution_id, executor, store):
    cached = store.get("execution", execution_id)
    if cached:
        record = ExecutionRecord.model_validate(cached)
        if record.bundle_digest != digest or record.task_id != task.id:
            raise ValueError("execution identity mismatch")
        if bundle_digest(store.bundle_path(digest)) != digest:
            raise ValueError("stored bundle digest mismatch")
        return record
    try:
        record = executor.execute(task, digest, execution_id)
    finally:
        config = store.get("experiment", "config")
        ledger = BudgetLedger(
            store.root / "budget.sqlite", ExperimentConfig.model_validate(config).max_usd
        )
        ledger.release(stage_group(execution_id))
    if (record.execution_id, record.task_id, record.bundle_digest) != (
        execution_id,
        task.id,
        digest,
    ):
        raise ValueError("execution identity mismatch")
    store.save_execution(record)
    return record


def run_experiment(tasks_by_split, config, store, executor, editor_factory, base_digest, arms=ARMS):
    requested = tuple(arms)
    arms = tuple(arm for arm in ARMS if arm in requested)
    if not arms or len(set(requested)) != len(requested) or not set(requested) <= set(ARMS):
        raise ValueError("choose unique supported experiment arms")
    if config.profile != "practical" or config.mode != "sequential":
        raise ValueError("OrderOps requires practical selection and sequential refinement")
    if set(tasks_by_split) != {"dev", "holdout"} or not all(tasks_by_split.values()):
        raise ValueError("nonempty dev and holdout splits required")
    for group in tasks_by_split.values():
        for task in group:
            source = task.source_root.resolve()
            if source.is_relative_to(store.root) or store.root.is_relative_to(source):
                raise ValueError("task sources and run directory must not overlap")
    admission = {
        "config": config.model_dump(mode="json"),
        "base": base_digest,
        "arms": list(arms),
        "inputs": {
            s: [{"task": t.model_dump(mode="json"), "sources": read_sources(t)} for t in group]
            for s, group in tasks_by_split.items()
        },
    }
    identity = fingerprint(admission)
    old = store.get("orderops", "admission")
    if old and old != identity:
        raise ValueError("OrderOps inputs or configuration changed; use a new run directory")
    store.create_experiment(config)
    store.put("orderops", "admission", identity)
    task_roots = [
        str(t.source_root.resolve().parent) for group in tasks_by_split.values() for t in group
    ]
    common = Path(os.path.commonpath(task_roots))
    protected = task_roots + ([str(common)] if common != Path(common.anchor) else [])
    store.put("orderops", "original-roots", protected)
    tasks = _snapshot(tasks_by_split, store)
    previous = store.get("orderops", "summary")
    if previous:
        for arm in previous["arms"].values():
            digest = arm["final_digest"]
            if bundle_digest(store.bundle_path(digest)) != digest:
                raise ValueError("stored bundle digest mismatch")
        return previous
    timed = TimedExecutor(executor, store)
    ledger = BudgetLedger(store.root / "budget.sqlite", config.max_usd)
    library = ExperienceLibrary(store)
    report = {
        "version": "orderops-v1",
        "arms": {},
        "selection": "model judgment; no hidden scores",
        "retry_control": "execution-attempt matched; editor cost is additional",
    }

    def row(task, start_digest, selected, chosen, records):
        # Both controller stage IDs and direct execution/reflect IDs are scoped by
        # the arm-prefixed task ID. Sum actual request usage, excluding allocations.
        alias = records[0].task_id if records else task.id
        total = ledger.usage_for(alias).usd + ledger.usage_for(f"orderops/{alias}").usd
        execution_usd = sum(r.usage.usd for r in records)
        return {
            "task_id": task.id,
            "start_digest": start_digest,
            "selected_digest": selected,
            "execution": chosen.model_dump(mode="json") if chosen else None,
            "execution_count": len(records),
            "execution_usd": str(execution_usd),
            "total_usd": str(total),
            "editor_usd": str(max(0, total - execution_usd)),
            "errors": [r.error for r in records if r.error],
            "execution_seconds": sum(
                store.get("orderops-time", r.execution_id) or 0 for r in records
            ),
        }

    for arm in arms:
        current, development = base_digest, []
        namespace = fingerprint([identity, arm])
        for source_task in tasks["dev"]:
            task = source_task.model_copy(update={"id": f"{arm}-{source_task.id}"})
            start = current if arm == "persistent" else base_digest
            chosen = None
            if arm in {"frozen", "retries"}:
                attempts = 1 if arm == "frozen" else 4 * config.k
                if arm == "retries" and "local" in report["arms"]:
                    local_row = next(
                        r
                        for r in report["arms"]["local"]["development"]
                        if r["task_id"] == source_task.id
                    )
                    attempts = max(1, local_row["execution_count"])
                records = [
                    _direct(task, start, f"orderops/{task.id}/rollout-{i}", timed, store)
                    for i in range(attempts)
                ]
                eligible = [r for r in records if _valid(task, r)]
                if len(eligible) == 1:
                    chosen = eligible[0]
                elif eligible:
                    key = f"{arm}/{source_task.id}"
                    chosen_id = store.get("orderops-choice", key)
                    if not chosen_id:
                        editor = editor_factory(task)
                        if hasattr(editor, "set_stage"):
                            editor.set_stage(f"orderops/{task.id}/select", 0, "", [])
                        try:
                            chosen_id, _ = editor.choose(task, eligible)
                        finally:
                            ledger.release(f"orderops/{task.id}/select")
                        if chosen_id not in {r.execution_id for r in eligible}:
                            raise ValueError("selector returned ineligible output")
                        store.put("orderops-choice", key, chosen_id)
                    chosen = next(r for r in eligible if r.execution_id == chosen_id)
            else:
                snapshot = (
                    library.snapshot(namespace, task.domain, task.ordinal)
                    if arm == "persistent"
                    else EvidenceSnapshot(digest="empty", task_id=task.id)
                )
                editor = editor_factory(task)
                failure_key = f"{task.id}/{start}"
                failed = store.get("orderops-failed", failure_key)
                if failed:
                    result = TaskResult.model_validate(failed)
                else:
                    try:
                        result = run_task(
                            task, config, store, timed, editor, start, snapshot.library
                        )
                    except RuntimeError as exc:
                        if not str(exc).startswith("no baseline execution delivered"):
                            raise
                        with store.transaction() as db:
                            stored = db.execute(
                                "SELECT value FROM records WHERE kind='execution'"
                            ).fetchall()
                        found = [ExecutionRecord.model_validate_json(value) for (value,) in stored]
                        result = TaskResult(
                            task_id=task.id,
                            executions={r.execution_id: r for r in found if r.task_id == task.id},
                            errors=[str(exc)],
                        )
                        store.put("orderops-failed", failure_key, result.model_dump(mode="json"))
                records = list(result.executions.values())
                selected = next(
                    (r for r in records if r.execution_id == result.selection.chosen_execution_id),
                    None,
                )
                if _valid(task, selected):
                    chosen = selected
                if arm == "persistent" and chosen and hasattr(editor, "reflect"):
                    key = f"{arm}/{source_task.id}"
                    reflection = store.get("orderops-reflection", key)
                    if reflection is None:
                        evidence = build_evidence(task, records, snapshot.library)
                        try:
                            if hasattr(editor, "set_stage"):
                                editor.set_stage(f"orderops/{task.id}/reflect", 0, "", [])
                            draft = editor.reflect(evidence, result)
                            validate_refs(draft.evidence_refs, evidence)
                            entry = ExperienceEntry(
                                namespace=namespace,
                                domain=task.domain,
                                task_id=task.id,
                                ordinal=task.ordinal,
                                bundle_digest=chosen.bundle_digest,
                                **draft.model_dump(),
                            )
                            reflection = {"entry": entry.model_dump(mode="json")}
                        except (ValueError, RuntimeError, OSError) as exc:
                            reflection = {"error": type(exc).__name__}
                        finally:
                            ledger.release(f"orderops/{task.id}/reflect")
                        store.put("orderops-reflection", key, reflection)
                    if "entry" in reflection:
                        library.append(ExperienceEntry.model_validate(reflection["entry"]))
            selected_digest = chosen.bundle_digest if chosen else start
            if arm == "persistent":
                current = selected_digest
            elif arm == "local":
                # The last local candidate is frozen for holdout; no cross-task carry.
                current = selected_digest
            development.append(row(source_task, start, selected_digest, chosen, records))
        heldout = []
        for source_task in tasks["holdout"]:
            task = source_task.model_copy(update={"id": f"{arm}-{source_task.id}"})
            record = _direct(task, current, f"orderops/{task.id}/frozen", timed, store)
            heldout.append(row(source_task, current, current, record, [record]))
        report["arms"][arm] = {
            "final_digest": current,
            "development": development,
            "heldout": heldout,
        }
        store.put("orderops-arm", arm, report["arms"][arm])
    report["budget"] = BudgetLedger(store.root / "budget.sqlite", config.max_usd).summary()
    store.put("orderops", "summary", report)
    return report
