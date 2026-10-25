"""Construct model evidence from explicit records, never arbitrary run directories."""

from pathlib import Path

from .contracts import EvidenceSnapshot, ExecutionRecord, TaskSpec
from .storage import fingerprint

_PRIVATE = {
    "rubrics.json",
    "rubric.json",
    "judged.json",
    "base_judged.json",
    "grader.json",
    "grading.json",
}


def permitted_name(rel: str) -> bool:
    return Path(rel).name.lower() not in _PRIVATE and not any(
        part.lower() in {"grading", "evaluation", "grader"} for part in Path(rel).parts
    )


def read_sources(task: TaskSpec) -> dict[str, str]:
    sources = {}
    total = 0
    for path in sorted(task.source_root.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlink source cannot enter evidence")
        if not path.is_file():
            continue
        rel = path.relative_to(task.source_root).as_posix()
        if not permitted_name(rel):
            raise ValueError("grader material in task source directory")
        total += path.stat().st_size
        if total > 2_000_000:
            raise ValueError("source evidence exceeds 2MB")
        sources[rel] = path.read_text(encoding="utf-8")
    return sources


def build_evidence(
    task: TaskSpec, records: list[ExecutionRecord], library: list[dict] | None = None
) -> EvidenceSnapshot:
    clean = []
    for record in records:
        traces = []
        for row in record.trace:
            traces.append({k: row[k] for k in ("id", "type", "content", "tool_calls") if k in row})
        artifacts = {
            k: v
            for k, v in record.artifacts.items()
            if k in task.output_paths and permitted_name(k)
        }
        clean.append(record.model_copy(update={"artifacts": artifacts, "trace": traces}))
    data = {
        "task": task.id,
        "records": [r.model_dump(mode="json") for r in clean],
        "library": library or [],
    }
    return EvidenceSnapshot(
        digest=fingerprint(data), task_id=task.id, records=clean, library=library or []
    )


def citeable_refs(evidence: EvidenceSnapshot) -> list[str]:
    """IDs a proposal or lesson may cite. Narrative stage text is not an ID."""
    refs = []
    for record in evidence.records:
        refs.extend(
            f"{record.execution_id}:trace:{row['id']}" for row in record.trace if "id" in row
        )
        refs.extend(f"{record.execution_id}:artifact:{rel}" for rel in record.artifacts)
    return refs


def validate_refs(refs: list[str], evidence: EvidenceSnapshot):
    if not set(refs) <= set(citeable_refs(evidence)):
        raise ValueError("proposal cites unavailable evidence")
