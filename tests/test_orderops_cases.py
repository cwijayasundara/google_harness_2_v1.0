import json

import pytest

from harness_clone.orderops.cases import build_case, generate_suite
from harness_clone.tasks import load_task


def state(**changes):
    return {
        "order_id": "O-1",
        "paid_pence": 12000,
        "cancelled_pence": 6000,
        "expected_units": 1,
        "counted_units": 1,
        "refunds_complete": True,
        "refunds": [],
        **changes,
    }


@pytest.mark.parametrize(
    "refunds,amount,issue,action",
    [
        ([], 6000, "refund_due", "refund"),
        ([{"id": "R-1", "amount_pence": 6000, "states": ["pending"]}], 0, "none", "no_action"),
        (
            [{"id": "R-1", "amount_pence": 6000, "states": ["pending", "completed"]}],
            0,
            "none",
            "no_action",
        ),
        (
            [{"id": "R-1", "amount_pence": 6000, "states": ["pending", "failed"]}],
            6000,
            "refund_due",
            "refund",
        ),
    ],
)
def test_latent_refund_answers_use_latest_transaction_state(refunds, amount, issue, action):
    sources, answer = build_case([state(refunds=refunds)], duplicate_events=True)
    assert answer["findings"] == [{"order_id": "O-1", "issue": issue, "amount_pence": amount}]
    assert answer["actions"][0]["action"] == action
    assert "refunds.csv" in sources


def test_missing_stock_requires_escalation():
    _, answer = build_case([state(counted_units=None)])
    assert answer["findings"][0]["issue"] == "insufficient_evidence"
    assert answer["actions"][0] == {"order_id": "O-1", "action": "escalate", "amount_pence": 0}


def test_generation_is_reproducible_disjoint_and_loadable(tmp_path):
    for name in ["a", "b"]:
        generate_suite(tmp_path / name, tmp_path / f"private-{name}", seed=7)
    a = json.loads((tmp_path / "private-a/answers.json").read_text())
    b = json.loads((tmp_path / "private-b/answers.json").read_text())
    assert a == b
    assert len(a["splits"]["dev"]) == 30
    assert len(a["splits"]["holdout"]) == 20
    for task_id in a["splits"]["dev"] + a["splits"]["holdout"]:
        task = load_task(tmp_path / "a" / task_id / "task.json")
        assert task.domain == "orderops"
        assert not (task.source_root / "answers.json").exists()
        assert (task.source_root / "orders.csv").read_bytes() == (
            tmp_path / "b" / task_id / "source/orders.csv"
        ).read_bytes()
    assert "transitions" not in {a["cases"][i]["family"] for i in a["splits"]["dev"]}
    assert "transitions" in {a["cases"][i]["family"] for i in a["splits"]["holdout"]}


@pytest.mark.parametrize("relationship", ["equal", "inside", "outside"])
def test_generation_refuses_overlapping_roots_before_writes(tmp_path, relationship):
    public = tmp_path / "public"
    private = {"equal": public, "inside": public / "private", "outside": tmp_path}[relationship]
    with pytest.raises(ValueError, match="disjoint"):
        generate_suite(public, private)
    assert not public.exists()


def test_generation_never_replaces_existing_data(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    (public / "keep").write_text("mine")
    with pytest.raises(ValueError, match="exist"):
        generate_suite(public, tmp_path / "private")
    assert (public / "keep").read_text() == "mine"
