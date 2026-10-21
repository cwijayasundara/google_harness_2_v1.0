import json
from pathlib import Path

import pytest

from harness_clone.portfolio_etoro import (
    ETORO_PNL_URL,
    EtoroClient,
    load_portfolio_csv,
    normalize_etoro_pnl,
)

FIXTURE = Path(__file__).parent / "fixtures" / "etoro_pnl.json"
FETCHED_AT = "2026-10-21T08:00:00Z"


def load_fixture():
    return json.loads(FIXTURE.read_text())


def write_csv(tmp_path):
    path = tmp_path / "portfolio.csv"
    path.write_text(
        "instrument,ticker,asset_class,units,open_rate,current_price\n"
        "Acme Corp,ACME,equity,10,100,120\n"
        "Cash,USD,cash,800,1,1\n"
    )
    return path


def test_normalize_computes_weights_and_cash():
    snap = normalize_etoro_pnl(load_fixture(), "2026-10-21T08:00:00Z")
    assert snap.positions[0].portfolio_weight == pytest.approx(0.6, abs=0.01)
    assert snap.cash_weight == pytest.approx(0.4, abs=0.01)


def test_normalize_missing_units_aborts():
    payload = load_fixture()
    del payload["positions"][0]["units"]
    with pytest.raises(ValueError, match="units"):
        normalize_etoro_pnl(payload, "2026-10-21T08:00:00Z")


def test_csv_matches_api_schema(tmp_path):
    snap = load_portfolio_csv(write_csv(tmp_path), "2026-10-21T08:00:00Z")
    assert (
        snap.model_dump()
        == normalize_etoro_pnl(load_fixture(), "2026-10-21T08:00:00Z").model_dump()
    )


def test_normalize_computes_unrealized_pnl_and_total_weight():
    snap = normalize_etoro_pnl(load_fixture(), FETCHED_AT)
    assert snap.currency == "USD"
    assert snap.fetched_at == FETCHED_AT
    assert snap.positions[0].unrealized_pnl_pct == pytest.approx(0.2)
    total = snap.cash_weight + sum(p.portfolio_weight for p in snap.positions)
    assert total == pytest.approx(1.0)


@pytest.mark.parametrize(
    "field", ["instrument", "ticker", "asset_class", "open_rate", "current_price"]
)
def test_normalize_missing_position_field_aborts(field):
    payload = load_fixture()
    del payload["positions"][0][field]
    with pytest.raises(ValueError, match=field):
        normalize_etoro_pnl(payload, FETCHED_AT)


def test_missing_currency_aborts():
    payload = load_fixture()
    del payload["currency"]
    with pytest.raises(ValueError, match="currency"):
        normalize_etoro_pnl(payload, FETCHED_AT)


def test_missing_cash_aborts():
    payload = load_fixture()
    del payload["cash"]
    with pytest.raises(ValueError, match="cash"):
        normalize_etoro_pnl(payload, FETCHED_AT)


def test_normalize_missing_positions_aborts():
    with pytest.raises(ValueError, match="positions"):
        normalize_etoro_pnl({"currency": "USD", "cash": 800.0}, FETCHED_AT)


def test_csv_missing_value_aborts(tmp_path):
    path = tmp_path / "portfolio.csv"
    path.write_text(
        "instrument,ticker,asset_class,units,open_rate,current_price\n"
        "Acme Corp,ACME,equity,,100,120\n"
    )
    with pytest.raises(ValueError, match="units"):
        load_portfolio_csv(path, FETCHED_AT)


def test_csv_missing_column_aborts(tmp_path):
    path = tmp_path / "portfolio.csv"
    path.write_text("instrument,ticker,asset_class,open_rate,current_price\nAcme,ACME,equity,1,1\n")
    with pytest.raises(ValueError, match="units"):
        load_portfolio_csv(path, FETCHED_AT)


def test_client_fetches_and_normalizes_with_injected_get():
    calls = []

    def fake_get(url, headers):
        calls.append((url, headers))
        return load_fixture()

    snap = EtoroClient(api_key="secret-key", get=fake_get).fetch_portfolio()
    assert calls == [(ETORO_PNL_URL, {"x-api-key": "secret-key", "Accept": "application/json"})]
    assert snap.positions[0].portfolio_weight == pytest.approx(0.6)
    assert snap.cash_weight == pytest.approx(0.4)
    assert snap.fetched_at


def test_client_from_env_reads_key_at_construction(monkeypatch):
    monkeypatch.setenv("ETORO_API_KEY", "env-key")
    client = EtoroClient.from_env(get=lambda url, headers: load_fixture())
    assert client.api_key == "env-key"


def test_client_from_env_missing_key_aborts(monkeypatch):
    monkeypatch.delenv("ETORO_API_KEY", raising=False)
    with pytest.raises(KeyError, match="ETORO_API_KEY"):
        EtoroClient.from_env(get=lambda url, headers: load_fixture())
