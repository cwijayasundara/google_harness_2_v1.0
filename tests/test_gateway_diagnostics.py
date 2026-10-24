import json
import threading
from decimal import Decimal

import httpx
import pytest

from harness_clone.contracts import MODEL, ExperimentConfig
from harness_clone.gateway import gateway_server
from harness_clone.storage import BudgetLedger


@pytest.mark.parametrize("extra,reason", [
    ({"max_completion_tokens": 100}, "unsupported unmetered request options"),
    ({"max_tokens": "private-sentinel"}, "gateway rejected request or upstream failed"),
])
def test_gateway_reports_controlled_policy_errors_without_echoing_values(
    tmp_path, monkeypatch, extra, reason,
):
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-not-a-key")
    db = tmp_path / "budget.sqlite"
    server = gateway_server(ExperimentConfig(max_usd="1"), db, "scoped-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = httpx.post(
            f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
            headers={"Authorization": "Bearer scoped-token"},
            json={"model": MODEL, "messages": [], **extra}, timeout=5, trust_env=False,
        )
        assert response.status_code == 400
        assert response.json()["error"]["message"] == reason
        ledger = BudgetLedger(db, Decimal("1"))
        with ledger.transaction() as conn:
            rows = conn.execute("SELECT value FROM records WHERE kind='gateway-error'").fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0])["reason"] == reason
        assert "private-sentinel" not in rows[0][0]
        assert ledger.summary()["spent"] == "0"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
