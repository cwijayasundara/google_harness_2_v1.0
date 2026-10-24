import json

import pytest

from harness_clone.contracts import TaskSpec
from harness_clone.orderops.score import score_case


@pytest.fixture
def case(tmp_path):
    (tmp_path / "orders.csv").write_text("order_id,paid_pence\nO-1,12000\n")
    task = TaskSpec(
        id="manual",
        domain="orderops",
        prompt="Investigate",
        source_root=tmp_path,
        output_paths=["findings.json", "proposed_actions.json", "report.md"],
    )
    answer = {
        "findings": [{"order_id": "O-1", "issue": "refund_due", "amount_pence": 6000}],
        "actions": [{"order_id": "O-1", "action": "refund", "amount_pence": 6000}],
    }
    artifacts = {
        "findings.json": json.dumps([{**answer["findings"][0], "evidence_refs": ["orders.csv:2"]}]),
        "proposed_actions.json": json.dumps(answer["actions"]),
        "report.md": "Refund £60, citing the order.",
    }
    return task, answer, artifacts


def test_correct_findings_and_actions_pass(case):
    task, answer, artifacts = case
    result = score_case(task, artifacts, answer)
    assert result["passed"]
    assert result["finding_accuracy"] == 1
    assert result["action_accuracy"] == 1


@pytest.mark.parametrize("amount", [5999, True, -1, 6000.0, "6000"])
def test_wrong_or_noninteger_amount_cannot_pass(case, amount):
    task, answer, artifacts = case
    rows = json.loads(artifacts["proposed_actions.json"])
    rows[0]["amount_pence"] = amount
    artifacts["proposed_actions.json"] = json.dumps(rows)
    assert not score_case(task, artifacts, answer)["passed"]


@pytest.mark.parametrize("mutation", ["empty", "duplicate", "unknown", "bad_ref", "missing"])
def test_incomplete_or_unsupported_delivery_fails(case, mutation):
    task, answer, artifacts = case
    rows = json.loads(artifacts["findings.json"])
    if mutation == "empty":
        rows = []
    elif mutation == "duplicate":
        rows *= 2
    elif mutation == "unknown":
        rows[0]["order_id"] = "O-unknown"
    elif mutation == "bad_ref":
        rows[0]["evidence_refs"] = ["orders.csv:99"]
    elif mutation == "missing":
        artifacts.pop("report.md")
    artifacts["findings.json"] = json.dumps(rows)
    assert not score_case(task, artifacts, answer)["passed"]


def test_unnecessary_refund_is_reported_as_false_action(case):
    task, answer, artifacts = case
    answer["actions"][0].update(action="no_action", amount_pence=0)
    result = score_case(task, artifacts, answer)
    assert result["false_active_actions"] == 1
    assert not result["passed"]


def test_false_actions_are_counted_even_when_other_artifacts_are_invalid(case):
    task, answer, artifacts = case
    answer["actions"][0].update(action="no_action", amount_pence=0)
    artifacts["report.md"] = ""
    result = score_case(task, artifacts, answer)
    assert not result["passed"]
    assert result["false_active_actions"] == 1


def test_malformed_nested_action_values_fail_without_crashing_grading(case):
    task, answer, artifacts = case
    artifacts["proposed_actions.json"] = json.dumps(
        [{"order_id": ["O-1"], "action": ["refund"], "amount_pence": 6000}]
    )
    assert not score_case(task, artifacts, answer)["passed"]
