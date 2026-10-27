import json
import urllib.request

from harness_clone.monitor import make_server, snapshot
from harness_clone.storage import RunStore


def test_snapshot_and_server_read_a_run_directory(tmp_path):
    store = RunStore(tmp_path)
    store.put("experiment", "config", {"mode": "parallel", "k": 1, "max_usd": "2"})
    store.put(
        "edit",
        "t/abc/inv1",
        {"label": "inv1", "scope": "INV", "status": "fallback", "attempts": 2, "error": "boom"},
    )
    store.put(
        "editor-response",
        "t/abc/inv1/attempt-0/response-0",
        {"finish_reason": "length", "message": {"content": "hi", "tool_calls": []}},
    )
    snap = snapshot(tmp_path)
    assert snap["edits"][0]["error"] == "boom"
    assert snap["editor_responses"][0]["finish_reason"] == "length"
    assert snap["finished"] is False

    server = make_server(tmp_path)
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert b"Harness run monitor" in urllib.request.urlopen(base + "/").read()
        data = json.load(urllib.request.urlopen(base + "/api/snapshot"))
        assert data["config"]["k"] == 1
    finally:
        server.shutdown()


def test_snapshot_of_missing_run_is_empty(tmp_path):
    assert snapshot(tmp_path / "nope")["executions"] == []


def test_snapshot_reports_unknown_usage_and_gateway_errors(tmp_path):
    from decimal import Decimal

    from harness_clone.contracts import Usage
    from harness_clone.storage import BudgetLedger

    ledger = BudgetLedger(tmp_path / "budget.sqlite", Decimal("2"))
    ledger.allocate("t/x/dom1", Decimal("0.25"))
    ledger.reserve("t/x/dom1/req", Decimal("0.01"), allocation_id="t/x/dom1")
    ledger.settle("t/x/dom1/req", Usage(known=False))
    ledger.put("gateway-error", "1", {"stage_id": "s", "reason": "blocked"})
    snap = snapshot(tmp_path)
    assert snap["unknown_usage"] is True
    assert snap["gateway_errors"] == [{"stage": "s", "reason": "blocked"}]
