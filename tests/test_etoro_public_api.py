import copy
import json
import uuid
from pathlib import Path

import pytest

from harness_clone.portfolio_etoro import (
    ETORO_API_BASE,
    EtoroClient,
    normalize_public_api,
)

FIXTURES = Path(__file__).parent / "fixtures"
FETCHED_AT = "2026-10-21T08:00:00Z"


def pnl():
    return json.loads((FIXTURES / "etoro_public_pnl.json").read_text())


def instruments():
    return json.loads((FIXTURES / "etoro_instruments.json").read_text())


def by_ticker(snap):
    return {p.ticker: p for p in snap.positions}


def test_normalizes_lots_mirrors_and_cash():
    snap = normalize_public_api(pnl(), instruments(), FETCHED_AT)
    held = by_ticker(snap)
    assert sorted(held) == ["AAPL", "BTC", "QQQ"]
    # Two AAPL lots aggregate: 15 units at a 160 average entry, now 200.
    aapl = held["AAPL"]
    assert (aapl.units, aapl.open_rate, aapl.current_price) == (15.0, 160.0, 200.0)
    assert aapl.unrealized_pnl_pct == pytest.approx(0.25)
    assert (aapl.instrument, aapl.asset_class) == ("Apple", "equity")
    assert held["QQQ"].asset_class == "etf" and held["BTC"].asset_class == "crypto"
    # Total 6000 = AAPL 3000 + QQQ 1000 + BTC 600 + cash (credit 1000 + mirror 400).
    assert aapl.portfolio_weight == pytest.approx(0.5)
    assert held["QQQ"].portfolio_weight == pytest.approx(1000 / 6000)
    assert snap.cash_weight == pytest.approx(1400 / 6000)
    assert (snap.currency, snap.fetched_at) == ("USD", FETCHED_AT)


def test_conversion_rate_drives_weights():
    payload = pnl()
    payload["clientPortfolio"]["mirrors"] = []
    payload["clientPortfolio"]["positions"] = [payload["clientPortfolio"]["positions"][0]]
    payload["clientPortfolio"]["positions"][0]["unrealizedPnL"]["closeConversionRate"] = 0.5
    snap = normalize_public_api(payload, instruments(), FETCHED_AT)
    # 10 units * 200 * 0.5 = 1000 USD against 1000 cash.
    assert snap.positions[0].portfolio_weight == pytest.approx(0.5)


def test_cash_only_account():
    payload = {"clientPortfolio": {"credit": 250.0, "positions": [], "mirrors": []}}
    snap = normalize_public_api(payload, {"instrumentDisplayDatas": []}, FETCHED_AT)
    assert snap.positions == [] and snap.cash_weight == 1.0


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda p: p["clientPortfolio"]["positions"][0].update(isBuy=False), "short"),
        (lambda p: p["clientPortfolio"]["positions"][0].update(leverage=5), "leverage"),
        (lambda p: p["clientPortfolio"]["positions"][0].pop("unrealizedPnL"), "closeRate"),
        (lambda p: p["clientPortfolio"]["positions"][0].update(openRate=0), "openRate"),
        (lambda p: p["clientPortfolio"].pop("credit"), "credit"),
        (lambda p: p["clientPortfolio"].update(accountCurrencyId=2), "currency"),
        (lambda p: p.pop("clientPortfolio"), "clientPortfolio"),
    ],
)
def test_unsupported_or_incomplete_payload_aborts(change, message):
    payload = pnl()
    change(payload)
    with pytest.raises(ValueError, match=message):
        normalize_public_api(payload, instruments(), FETCHED_AT)


def test_short_positions_are_named_with_a_csv_hint():
    payload = pnl()
    payload["clientPortfolio"]["positions"][2]["isBuy"] = False
    with pytest.raises(ValueError, match=r"BTC.*--csv"):
        normalize_public_api(payload, instruments(), FETCHED_AT)


def test_unknown_or_unsupported_instruments_abort():
    lookup = instruments()
    lookup["instrumentDisplayDatas"] = lookup["instrumentDisplayDatas"][:2]
    with pytest.raises(ValueError, match="100000"):
        normalize_public_api(pnl(), lookup, FETCHED_AT)
    forex = instruments()
    forex["instrumentDisplayDatas"][0]["instrumentTypeID"] = 1
    with pytest.raises(ValueError, match="instrument type 1"):
        normalize_public_api(pnl(), forex, FETCHED_AT)


def test_client_sends_both_keys_and_a_fresh_request_id():
    calls = []

    def fake_get(url, headers):
        calls.append((url, dict(headers)))
        return copy.deepcopy(pnl()) if "/pnl" in url else instruments()

    snap = EtoroClient(api_key="app-key", user_key="user-key", get=fake_get).fetch_portfolio()
    assert [url for url, _ in calls] == [
        f"{ETORO_API_BASE}/trading/info/real/pnl",
        f"{ETORO_API_BASE}/market-data/instruments?instrumentIds=1001,3006,100000",
    ]
    for _, headers in calls:
        assert headers["x-api-key"] == "app-key" and headers["x-user-key"] == "user-key"
        uuid.UUID(headers["x-request-id"])
    assert calls[0][1]["x-request-id"] != calls[1][1]["x-request-id"]
    assert sorted(by_ticker(snap)) == ["AAPL", "BTC", "QQQ"]


def test_demo_environment_uses_demo_pnl():
    urls = []

    def fake_get(url, headers):
        urls.append(url)
        return pnl() if "/pnl" in url else instruments()

    EtoroClient("a", "u", fake_get, environment="demo").fetch_portfolio()
    assert urls[0] == f"{ETORO_API_BASE}/trading/info/demo/pnl"


def test_cash_only_account_skips_instrument_lookup():
    urls = []

    def fake_get(url, headers):
        urls.append(url)
        return {"clientPortfolio": {"credit": 10.0, "positions": []}}

    EtoroClient("a", "u", fake_get).fetch_portfolio()
    assert len(urls) == 1


def test_from_env_reads_both_keys_and_environment(monkeypatch):
    monkeypatch.setenv("ETORO_API_KEY", "app")
    monkeypatch.setenv("ETORO_USER_KEY", "user")
    monkeypatch.setenv("ETORO_ENVIRONMENT", "demo")
    client = EtoroClient.from_env(get=lambda url, headers: {})
    assert (client.api_key, client.user_key, client.environment) == ("app", "user", "demo")


@pytest.mark.parametrize("missing", ["ETORO_API_KEY", "ETORO_USER_KEY"])
def test_from_env_requires_both_keys(monkeypatch, missing):
    monkeypatch.setenv("ETORO_API_KEY", "app")
    monkeypatch.setenv("ETORO_USER_KEY", "user")
    monkeypatch.delenv(missing)
    with pytest.raises(ValueError, match=missing):
        EtoroClient.from_env(get=lambda url, headers: {})


def test_from_env_rejects_unknown_environment(monkeypatch):
    monkeypatch.setenv("ETORO_API_KEY", "app")
    monkeypatch.setenv("ETORO_USER_KEY", "user")
    monkeypatch.setenv("ETORO_ENVIRONMENT", "paper")
    with pytest.raises(ValueError, match="ETORO_ENVIRONMENT"):
        EtoroClient.from_env(get=lambda url, headers: {})
