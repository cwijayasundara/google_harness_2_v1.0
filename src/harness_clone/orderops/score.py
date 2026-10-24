"""Offline scoring. Never imported by online editing or execution modules."""

import json
from decimal import Decimal
from pathlib import Path

from ..contracts import TaskSpec
from ..evidence import read_sources
from ..storage import RunStore, fingerprint

from .contracts import validate_artifacts


def _false_actions(artifacts, expected):
    """Count parseable wrong active proposals even if other artifacts are invalid."""
    wanted = {r["order_id"]: r for r in expected["actions"]}
    try:
        actions = json.loads(artifacts.get("proposed_actions.json", "null"))
    except (ValueError, TypeError):
        return 0
    if not isinstance(actions, list):
        return 0
    count, seen = 0, set()
    for action in actions:
        if not isinstance(action, dict) or action.get("action") not in (
            "refund",
            "investigate_stock",
        ):
            continue
        order_id = action.get("order_id")
        if not isinstance(order_id, str):
            count += 1
            continue
        actual = {k: action.get(k) for k in ("order_id", "action", "amount_pence")}
        if (
            actual != wanted.get(order_id)
            or type(actual["amount_pence"]) is not int
            or order_id in seen
        ):
            count += 1
        seen.add(order_id)
    return count


def score_case(task, artifacts, expected):
    result = dict(
        passed=False,
        valid=False,
        finding_accuracy=0.0,
        action_accuracy=0.0,
        false_active_actions=_false_actions(artifacts, expected),
        evidence_reference_validity=0.0,
    )
    try:
        findings, actions = validate_artifacts(task, artifacts)
    except (ValueError, KeyError, TypeError) as exc:
        result["error"] = str(exc)[:300]
        return result
    wanted_findings = {r["order_id"]: r for r in expected["findings"]}
    wanted_actions = {r["order_id"]: r for r in expected["actions"]}
    if not wanted_findings or set(wanted_findings) != set(wanted_actions):
        raise ValueError("invalid private answer key")
    good_findings = sum(
        {k: v for k, v in f.model_dump().items() if k != "evidence_refs"}
        == wanted_findings.get(f.order_id)
        for f in findings
    )
    good_actions = sum(a.model_dump() == wanted_actions.get(a.order_id) for a in actions)
    n = len(wanted_findings)
    result.update(
        valid=True,
        finding_accuracy=good_findings / n,
        action_accuracy=good_actions / n,
        evidence_reference_validity=1.0,
        passed=good_findings == n and good_actions == n and len(findings) == n,
    )
    return result


def evaluate_experiment(run_dir: Path, answers_path: Path, output: Path):
    run_dir, output = Path(run_dir).resolve(), Path(output).resolve()
    if output == Path(answers_path).resolve():
        raise ValueError("evaluation output must not overwrite the answer key")
    if output.is_relative_to(run_dir):
        raise ValueError("evaluation output must be outside the online run directory")
    if not (run_dir / "run.sqlite").is_file():
        raise ValueError("run directory does not contain an experiment")
    store = RunStore(run_dir)
    summary = store.get("orderops", "summary")
    if not summary:
        raise ValueError("OrderOps experiment has not finished")
    tasks = {
        s: {t["id"]: TaskSpec.model_validate(t) for t in group}
        for s, group in store.get("orderops", "tasks").items()
    }
    original = store.get("orderops", "original-roots") or []
    if any(output.is_relative_to(Path(root).resolve()) for root in original):
        raise ValueError("evaluation output must be outside task source directories")
    answers = json.loads(Path(answers_path).read_text())
    if answers["version"] != "orderops-v1":
        raise ValueError("unsupported private answer version")
    for split in tasks:
        if set(answers["splits"][split]) != set(tasks[split]):
            raise ValueError("answer key does not match experiment tasks")
        for task_id, task in tasks[split].items():
            expected_hash = answers["cases"][task_id].get("source_digest")
            if expected_hash and expected_hash != fingerprint(read_sources(task)):
                raise ValueError("answer key source digest mismatch")
    report = {
        "arms": {},
        "paired_heldout": [],
        "budget": summary["budget"],
        "note": "Descriptive pilot. Row-reference validity does not prove semantic support.",
    }
    for arm_name, arm in summary["arms"].items():
        report["arms"][arm_name] = {}
        for split, rows_key in [("dev", "development"), ("holdout", "heldout")]:
            by_id = {r["task_id"]: r for r in arm[rows_key]}
            results = []
            for task_id, task in tasks[split].items():
                row = by_id.get(task_id, {})
                execution = row.get("execution") or {}
                delivered = (
                    execution.get("delivered", False) and execution.get("status") == "completed"
                )
                scored = score_case(
                    task,
                    execution.get("artifacts", {}),
                    answers["cases"][task_id],
                )
                if not delivered:
                    scored.update(
                        passed=False,
                        valid=False,
                        finding_accuracy=0.0,
                        action_accuracy=0.0,
                        evidence_reference_validity=0.0,
                    )
                results.append({"task_id": task_id, "delivered": delivered, **scored})
            n = len(results)
            report["arms"][arm_name][rows_key] = {
                "cases": n,
                "passed": sum(r["passed"] for r in results),
                "success_rate": sum(r["passed"] for r in results) / n,
                "delivery_coverage": sum(r["delivered"] for r in results) / n,
                "finding_accuracy": sum(r["finding_accuracy"] for r in results) / n,
                "action_accuracy": sum(r["action_accuracy"] for r in results) / n,
                "false_active_actions": sum(r["false_active_actions"] for r in results),
                "evidence_reference_validity": sum(
                    r["evidence_reference_validity"] for r in results
                )
                / n,
                "execution_seconds": sum(r["execution_seconds"] for r in by_id.values()),
                "execution_usd": str(sum(Decimal(r["execution_usd"]) for r in by_id.values())),
                "editor_usd": str(sum(Decimal(r["editor_usd"]) for r in by_id.values())),
                "total_usd": str(sum(Decimal(r["total_usd"]) for r in by_id.values())),
                "results": results,
            }
    for task_id in tasks["holdout"]:
        report["paired_heldout"].append(
            {
                "task_id": task_id,
                **{
                    name: next(
                        r["passed"] for r in arm["heldout"]["results"] if r["task_id"] == task_id
                    )
                    for name, arm in report["arms"].items()
                },
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2))
    return report
