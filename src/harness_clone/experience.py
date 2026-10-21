"""Causal per-domain experience, separate from execution memory and checkpoints."""

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from .bundles import bundle_digest
from .contracts import EvidenceSnapshot, ExperienceEntry
from .controller import run_task
from .evidence import build_evidence, read_sources, validate_refs
from .storage import fingerprint


class ExperienceLibrary:
    def __init__(self, store):
        self.store = store

    def append(self, entry: ExperienceEntry) -> str:
        if not entry.cue.strip() or not entry.lesson.strip():
            raise ValueError("experience requires a cue and supported lesson")
        if bundle_digest(self.store.bundle_path(entry.bundle_digest)) != entry.bundle_digest:
            raise ValueError("experience bundle digest mismatch")
        key = fingerprint([entry.namespace, entry.domain, entry.ordinal, entry.task_id])
        value = entry.model_dump(mode="json")
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT value FROM records WHERE kind='experience' AND id=?", (key,)
            ).fetchone()
            if row and json.loads(row[0]) != value:
                raise ValueError("conflicting experience republication")
            db.execute(
                "INSERT OR IGNORE INTO records VALUES('experience',?,?)",
                (key, json.dumps(value, sort_keys=True)),
            )
        return key

    def snapshot(self, namespace: str, domain: str, before_ordinal: int) -> EvidenceSnapshot:
        with self.store.transaction() as db:
            rows = db.execute("SELECT value FROM records WHERE kind='experience'").fetchall()
        entries = [ExperienceEntry.model_validate_json(row[0]) for row in rows]
        selected = sorted(
            (
                e
                for e in entries
                if e.namespace == namespace and e.domain == domain and e.ordinal < before_ordinal
            ),
            key=lambda e: (e.ordinal, e.task_id),
        )
        for entry in selected:
            if bundle_digest(self.store.bundle_path(entry.bundle_digest)) != entry.bundle_digest:
                raise ValueError("experience bundle digest mismatch")
        data = [e.model_dump(mode="json") for e in selected]
        return EvidenceSnapshot(digest=fingerprint(data), task_id="library", library=data)


def stream_signature(tasks) -> str:
    return fingerprint(
        [{"task": t.model_dump(mode="json"), "sources": read_sources(t)} for t in tasks]
    )


def check_stream(store, tasks, base_digest, *, learn=True) -> None:
    """Reject changed stream inputs before any caller persists new metadata."""
    previous = store.get("stream", "signature")
    if previous and previous != {
        "signature": stream_signature(tasks),
        "learn": learn,
        "base": base_digest,
    }:
        raise ValueError("stream inputs changed; use a new run directory")


def run_stream(
    tasks, config, store, executor, editor_factory, base_digest, *, learn=True, workers=1
):
    store.create_experiment(config)
    signature = stream_signature(tasks)
    check_stream(store, tasks, base_digest, learn=learn)
    store.put("stream", "signature", {"signature": signature, "learn": learn, "base": base_digest})
    namespace = fingerprint({"config": config.model_dump(mode="json"), "base": base_digest})
    lib = ExperienceLibrary(store)
    groups = defaultdict(list)
    for task in tasks:
        groups[task.domain].append(task)
    for group in groups.values():
        if len({t.ordinal for t in group}) != len(group) or len({t.id for t in group}) != len(
            group
        ):
            raise ValueError("domain tasks need unique IDs and ordinals")

    def domain(group):
        output = []
        for task in sorted(group, key=lambda t: t.ordinal):
            key = fingerprint([namespace, task.domain, task.id, task.ordinal])
            saved = store.get("library-snapshot", key)
            if saved is None:
                saved = lib.snapshot(namespace, task.domain, task.ordinal).model_dump(mode="json")
                store.put("library-snapshot", key, saved)
            snapshot = EvidenceSnapshot.model_validate(saved)
            editor = editor_factory(task)
            result = run_task(task, config, store, executor, editor, base_digest, snapshot.library)
            if learn and result.selection.chosen_execution_id and hasattr(editor, "reflect"):
                adopted = next(
                    r
                    for r in result.executions.values()
                    if r.execution_id == result.selection.chosen_execution_id
                )
                reflection = store.get("reflection", key)
                if reflection is None:
                    evidence = build_evidence(
                        task, list(result.executions.values()), snapshot.library
                    )
                    try:
                        draft = editor.reflect(evidence, result)
                        validate_refs(draft.evidence_refs, evidence)
                        entry = ExperienceEntry(
                            namespace=namespace,
                            domain=task.domain,
                            task_id=task.id,
                            ordinal=task.ordinal,
                            bundle_digest=adopted.bundle_digest,
                            **draft.model_dump(),
                        )
                        reflection = {"entry": entry.model_dump(mode="json")}
                    except (ValueError, RuntimeError, OSError) as exc:
                        reflection = {"error": type(exc).__name__}
                    store.put("reflection", key, reflection)
                if "entry" in reflection:
                    lib.append(ExperienceEntry.model_validate(reflection["entry"]))
            output.append(result)
        return output

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            batches = list(pool.map(domain, [groups[d] for d in sorted(groups)]))
    else:
        batches = [domain(groups[d]) for d in sorted(groups)]
    return [result for batch in batches for result in batch]
