"""Offline exact/JSON artifact assertions, never supplied to refinement agents."""

import json
from pathlib import Path

from .contracts import TaskResult


def evaluate(store, expectations: dict, output: Path) -> dict:
    results = [TaskResult.model_validate(r) for r in store.get("runtime", "results") or []]
    rows = []
    for result in results:
        expected = expectations.get(result.task_id)
        chosen = next(
            (
                r
                for r in result.executions.values()
                if r.execution_id == result.selection.chosen_execution_id
            ),
            None,
        )
        if expected is None or chosen is None:
            rows.append({"task": result.task_id, "status": "unscored"})
            continue
        checks = []
        for path, value in expected.items():
            actual = chosen.artifacts.get(path)
            if isinstance(value, (dict, list)):
                try:
                    actual = json.loads(actual) if actual is not None else None
                except (TypeError, ValueError):
                    actual = None
            checks.append(actual == value)
        rows.append(
            {"task": result.task_id, "status": "passed" if checks and all(checks) else "failed"}
        )
    scored = sum(row["status"] != "unscored" for row in rows)
    report = {
        "tasks": len(rows),
        "scored": scored,
        "passed": sum(row["status"] == "passed" for row in rows),
        "coverage": scored / len(rows) if rows else 0.0,
        "results": rows,
    }
    output = output.resolve()
    # Avoid overwriting online storage or task evidence with hidden expectations/results.
    if output.is_relative_to(store.root):
        raise ValueError("evaluation output must be outside the online run directory")
    for task in store.get("runtime", "tasks") or []:
        source_root = Path(task["source_root"]).resolve()
        if output.is_relative_to(source_root):
            raise ValueError("evaluation output must be outside every task source directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2))
    return report
