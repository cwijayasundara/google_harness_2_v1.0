"""Deterministic synthetic transactions rendered separately from latent answers.

This host-only generator is never mounted in executor/editor containers.
"""

import csv
import io
import json
import random
import shutil
import tempfile
from pathlib import Path

from ..contracts import TaskSpec
from ..storage import fingerprint
from .contracts import Action, Finding

VERSION = "orderops-v1"
DEV = ("shortfall", "duplicate", "pending", "settled", "stock", "missing")
HOLDOUT = ("transitions", "renamed", *DEV)
POLICY = """OrderOps policy v1. All currency amounts are integer GBP pence.
Each order requires one finding and one proposed action, including no-action orders.
Refund liability is cancelled_pence. Deduplicate event_id retransmissions. Group
refund events by transaction_id and use the state at the greatest sequence number.
Completed refunds settle liability; pending refunds reserve it; failed refunds
neither settle nor reserve. Refund max(0, liability - settled - reserved).
If refunds_complete is false or counted_units is blank, evidence is insufficient:
issue insufficient_evidence, action escalate, amount_pence 0. Otherwise a positive
refund shortfall takes priority: issue refund_due, action refund, amount shortfall.
Otherwise if counted_units < expected_units, issue stock_shortage, action
investigate_stock, amount_pence 0. Otherwise issue none, action no_action, amount 0.
Propose actions only; no real payments or stock changes are executed.
Evidence references use filename.csv:row_number, counting the header as row 1.
schema.json maps canonical field names to actual CSV column names.
"""


def _answer(states):
    findings, actions = [], []
    for s in states:
        reserved = sum(
            r["amount_pence"] for r in s["refunds"] if r["states"][-1] in {"pending", "completed"}
        )
        amount = max(0, s["cancelled_pence"] - reserved)
        if not s["refunds_complete"] or s["counted_units"] is None:
            issue, action, amount = "insufficient_evidence", "escalate", 0
        elif amount:
            issue, action = "refund_due", "refund"
        elif s["counted_units"] < s["expected_units"]:
            issue, action, amount = "stock_shortage", "investigate_stock", 0
        else:
            issue, action, amount = "none", "no_action", 0
        findings.append(dict(order_id=s["order_id"], issue=issue, amount_pence=amount))
        actions.append(dict(order_id=s["order_id"], action=action, amount_pence=amount))
    return {"findings": findings, "actions": actions}


def build_case(states, *, duplicate_events=False, renamed=False):
    """Return public source strings and private answers from latent order state."""
    orders, refunds, inventory = [], [], []
    for s in states:
        orders.append(
            {k: s[k] for k in ("order_id", "paid_pence", "cancelled_pence", "refunds_complete")}
        )
        inventory.append({k: s[k] for k in ("order_id", "expected_units", "counted_units")})
        for r in s["refunds"]:
            for sequence, status in enumerate(r["states"], 1):
                row = dict(
                    event_id=f"{r['id']}-{sequence}",
                    transaction_id=r["id"],
                    order_id=s["order_id"],
                    state=status,
                    sequence=sequence,
                    amount_pence=r["amount_pence"],
                )
                refunds.append(row)
                if duplicate_events:
                    refunds.append(dict(row))
    # Rows intentionally need not be chronological; use sequence, not file position.
    refunds.reverse()
    tables = {
        "orders.csv": (orders, ["order_id", "paid_pence", "cancelled_pence", "refunds_complete"]),
        "refunds.csv": (
            refunds,
            ["event_id", "transaction_id", "order_id", "state", "sequence", "amount_pence"],
        ),
        "inventory.csv": (inventory, ["order_id", "expected_units", "counted_units"]),
    }
    sources, schema = {}, {}
    for name, (rows, columns) in tables.items():
        mapping = {c: f"field_{i}" if renamed else c for i, c in enumerate(columns)}
        schema[name] = mapping
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=list(mapping.values()), lineterminator="\n")
        writer.writeheader()
        writer.writerows({mapping[k]: v for k, v in row.items()} for row in rows)
        sources[name] = stream.getvalue()
    sources["schema.json"] = json.dumps(schema, indent=2)
    sources["policy.md"] = POLICY
    sources["response_contract.json"] = json.dumps(
        {
            "findings.json": {"type": "array", "items": Finding.model_json_schema()},
            "proposed_actions.json": {"type": "array", "items": Action.model_json_schema()},
            "report.md": "Nonempty concise explanation with supporting row references.",
        },
        indent=2,
    )
    return sources, _answer(states)


def _states(rng, family):
    states = []
    for i in range(3):
        due = rng.randint(10, 100) * 100
        s = dict(
            order_id=f"O-{rng.randrange(100000, 999999)}-{i}",
            paid_pence=due * 2,
            cancelled_pence=due,
            expected_units=2,
            counted_units=2,
            refunds_complete=True,
            refunds=[],
        )
        mode = family if i == 0 else rng.choice(DEV)
        status = {
            "duplicate": ["completed"],
            "pending": ["pending"],
            "settled": ["completed"],
            "stock": ["completed"],
            "missing": ["completed"],
            "transitions": ["pending", "failed"],
            "renamed": ["pending", "completed"],
        }.get(mode)
        if status:
            s["refunds"] = [dict(id=f"R-{i}", amount_pence=due, states=status)]
        if mode == "stock":
            s["counted_units"] = 1
        if mode == "missing":
            if rng.choice([True, False]):
                s["counted_units"] = None
            else:
                s["refunds_complete"] = False
        if mode == "shortfall" and rng.choice([True, False]):
            s["refunds"] = [dict(id=f"R-{i}", amount_pence=due // 2, states=["completed"])]
        states.append(s)
    return states


def generate_suite(
    public_root: Path, private_root: Path, *, seed=42, dev_count=30, holdout_count=20
):
    public_root, private_root = Path(public_root).resolve(), Path(private_root).resolve()
    if public_root.is_relative_to(private_root) or private_root.is_relative_to(public_root):
        raise ValueError("public tasks and private answers must be disjoint")
    if public_root.exists() or private_root.exists():
        raise ValueError("generation destinations must not exist")
    if dev_count < 1 or holdout_count < 1:
        raise ValueError("each split must contain at least one case")
    rng = random.Random(seed)
    manifest = dict(version=VERSION, seed=seed, splits={"dev": [], "holdout": []}, cases={})
    public_root.parent.mkdir(parents=True, exist_ok=True)
    private_root.parent.mkdir(parents=True, exist_ok=True)
    # Stage the complete dataset before publishing either destination.
    with tempfile.TemporaryDirectory(dir=public_root.parent) as temp:
        staging = Path(temp) / "tasks"
        staging.mkdir()
        for split, count, families in [
            ("dev", dev_count, DEV),
            ("holdout", holdout_count, HOLDOUT),
        ]:
            for ordinal in range(count):
                family = families[ordinal % len(families)]
                task_id = f"{split}-{ordinal:03d}"
                sources, answer = build_case(
                    _states(rng, family),
                    duplicate_events=family == "duplicate",
                    renamed=family == "renamed",
                )
                root = staging / task_id
                (root / "source").mkdir(parents=True)
                for name, content in sources.items():
                    (root / "source" / name).write_text(content)
                task = TaskSpec(
                    id=task_id,
                    domain="orderops",
                    ordinal=ordinal,
                    prompt="Investigate all orders using the supplied policy and "
                    "response contract. Write findings.json, proposed_actions.json "
                    "and report.md. Cite CSV rows. Propose actions only.",
                    source_root=Path("source"),
                    output_paths=["findings.json", "proposed_actions.json", "report.md"],
                )
                (root / "task.json").write_text(task.model_dump_json(indent=2))
                manifest["splits"][split].append(task_id)
                manifest["cases"][task_id] = {
                    "family": family,
                    "source_digest": fingerprint(sources),
                    **answer,
                }
        (staging / "suite.json").write_text(
            json.dumps({"version": VERSION, "splits": manifest["splits"]}, indent=2)
        )
        shutil.move(str(staging), public_root)
    private_root.mkdir()
    (private_root / "answers.json").write_text(json.dumps(manifest, indent=2))
    return {
        "tasks_dir": str(public_root),
        "answers": str(private_root / "answers.json"),
        "development_cases": dev_count,
        "heldout_cases": holdout_count,
    }
