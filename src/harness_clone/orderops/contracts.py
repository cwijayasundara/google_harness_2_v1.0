"""Public output contracts; no answer keys or grading logic."""

import csv
import io
import json
from typing import Literal

from pydantic import Field, TypeAdapter

from ..contracts import Record
from ..evidence import read_sources


class Finding(Record):
    order_id: str
    issue: Literal["refund_due", "stock_shortage", "none", "insufficient_evidence"]
    amount_pence: int = Field(strict=True, ge=0)
    evidence_refs: list[str] = Field(min_length=1)


class Action(Record):
    order_id: str
    action: Literal["refund", "investigate_stock", "no_action", "escalate"]
    amount_pence: int = Field(strict=True, ge=0)


def validate_artifacts(task, artifacts):
    findings = TypeAdapter(list[Finding]).validate_json(artifacts["findings.json"])
    actions = TypeAdapter(list[Action]).validate_json(artifacts["proposed_actions.json"])
    if not artifacts["report.md"].strip():
        raise ValueError("report is empty")
    sources = read_sources(task)
    schema = json.loads(sources.get("schema.json", "{}"))
    id_column = schema.get("orders.csv", {}).get("order_id", "order_id")
    orders = list(csv.DictReader(io.StringIO(sources["orders.csv"])))
    ids = {row[id_column] for row in orders}
    for rows in (findings, actions):
        if len(rows) != len(ids) or {row.order_id for row in rows} != ids:
            raise ValueError("outputs must contain each order exactly once")
    available = set()
    for name, content in sources.items():
        if name.endswith(".csv"):
            available.update(
                f"{name}:{i}" for i, _ in enumerate(csv.reader(io.StringIO(content)), 1) if i > 1
            )
    for finding in findings:
        if not set(finding.evidence_refs) <= available:
            raise ValueError("unavailable row evidence reference")
    return findings, actions
