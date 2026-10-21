from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from harness_clone.contracts import Usage


def ledger(tmp_path, cap="1"):
    from harness_clone.storage import BudgetLedger

    return BudgetLedger(tmp_path / "budget.sqlite", Decimal(cap))


def test_parallel_reservations_cannot_exceed_cap(tmp_path):
    b = ledger(tmp_path)

    def reserve(i):
        try:
            b.reserve(str(i), Decimal("0.6"))
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(reserve, [1, 2])) == 1


def test_settlement_is_idempotent(tmp_path):
    b = ledger(tmp_path)
    b.reserve("one", Decimal("0.5"))
    u = Usage(usd=Decimal("0.2"))
    b.settle("one", u)
    b.settle("one", u)
    assert b.summary()["spent"] == "0.2"
    with pytest.raises(ValueError):
        b.settle("one", Usage(usd=Decimal("0.3")))
    b.reserve("two", Decimal("0.8"))


def test_zero_cap_unknown_usage_and_overrun(tmp_path):
    with pytest.raises(ValueError):
        ledger(tmp_path / "zero", "0").reserve("one", Decimal("0.1"))
    b = ledger(tmp_path)
    b.reserve("one", Decimal("0.5"))
    b.settle("one", Usage(known=False))
    with pytest.raises(ValueError, match="unknown"):
        b.reserve("two", Decimal("0.1"))
    b = ledger(tmp_path / "overrun")
    b.reserve("one", Decimal("0.5"))
    b.settle("one", Usage(usd=Decimal("1.1")))
    assert b.summary()["spent"] == "1.1"
    with pytest.raises(ValueError):
        b.reserve("two", Decimal("0.01"))


def test_per_stage_accounting_includes_pending_and_actual_usage(tmp_path):
    b = ledger(tmp_path)
    b.reserve("run-a/request-1", Decimal("0.1"))
    b.settle("run-a/request-1", Usage(input_tokens=10, output_tokens=5, usd=Decimal("0.01")))
    b.reserve("run-b/request-1", Decimal("0.1"))
    b.settle("run-b/request-1", Usage(usd=Decimal("0.02")))
    assert b.usage_for("run-a").usd == Decimal("0.01")
    b.reserve("run-a/request-2", Decimal("0.1"))
    assert b.usage_for("run-a").known is False
