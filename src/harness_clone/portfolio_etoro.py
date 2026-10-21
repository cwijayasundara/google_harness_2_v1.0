"""eToro portfolio fetching and CSV fallback, normalized to PortfolioSnapshot.

The live path reads the eToro Public API (read-only): the account PnL route
for holdings and cash, then the instruments route to turn instrument ids into
tickers and asset classes. Requests authenticate with the application key
(``x-api-key``) and user key (``x-user-key``) plus a fresh ``x-request-id``.
``normalize_etoro_pnl`` handles the simplified ticker-keyed shape the CSV
fallback produces. No credential is read at import time.
"""

import csv
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from harness_clone.contracts import PortfolioPosition, PortfolioSnapshot

ETORO_API_BASE = "https://public-api.etoro.com/api/v1"
PNL_PATHS = {"real": "/trading/info/real/pnl", "demo": "/trading/info/demo/pnl"}
INSTRUMENTS_PATH = "/market-data/instruments"
# eToro instrumentTypeID -> snapshot asset class; other types (forex, commodities,
# indices, CFDs, bonds, options) are outside the agent's research scope.
INSTRUMENT_TYPE_CLASSES = {5: "equity", 6: "etf", 10: "crypto"}
# The API documents credit and position amounts in USD; 1 is its USD currency id.
USD_CURRENCY_ID = 1

_POSITION_FIELDS = (
    "instrument",
    "ticker",
    "asset_class",
    "units",
    "open_rate",
    "current_price",
)
_CSV_HEADER = ["instrument", "ticker", "asset_class", "units", "open_rate", "current_price"]


def _required(mapping: dict, field: str):
    value = mapping.get(field)
    if value is None or value == "":
        raise ValueError(f"missing required field: {field}")
    return value


def _cash_amount(payload: dict, positions: list[dict]) -> float:
    for key in ("cash", "cash_balance", "credit"):
        if payload.get(key) is not None:
            return float(payload[key])
    cash_rows = [p for p in positions if p.get("asset_class") == "cash"]
    if not cash_rows:
        raise ValueError("missing required field: cash")
    return sum(float(p["units"]) * float(p["current_price"]) for p in cash_rows)


def normalize_etoro_pnl(payload: dict, fetched_at: str) -> PortfolioSnapshot:
    """Normalize an eToro-style PnL payload; missing fields abort by name."""
    body = payload
    for wrapper in ("clientPortfolio", "portfolio"):
        if isinstance(body.get(wrapper), dict):
            body = body[wrapper]
            break
    raw_positions = _required(body, "positions")
    if not isinstance(raw_positions, list):
        raise ValueError("missing required field: positions")

    parsed: list[dict] = []
    for raw in raw_positions:
        if not isinstance(raw, dict):
            raise ValueError("missing required field: positions")
        parsed.append({field: _required(raw, field) for field in _POSITION_FIELDS})

    cash = _cash_amount(body, parsed)
    holdings = [p for p in parsed if p["asset_class"] != "cash"]
    values = [float(p["units"]) * float(p["current_price"]) for p in holdings]
    total = cash + sum(values)
    if total <= 0:
        raise ValueError("total account value must be positive")

    positions = []
    for holding, value in zip(holdings, values, strict=True):
        open_rate = float(holding["open_rate"])
        if open_rate == 0:
            raise ValueError("missing required field: open_rate")
        current_price = float(holding["current_price"])
        positions.append(
            PortfolioPosition(
                instrument=str(holding["instrument"]),
                ticker=str(holding["ticker"]),
                asset_class=holding["asset_class"],
                units=float(holding["units"]),
                open_rate=open_rate,
                current_price=current_price,
                unrealized_pnl_pct=(current_price - open_rate) / open_rate,
                portfolio_weight=value / total,
            )
        )
    return PortfolioSnapshot(
        fetched_at=fetched_at,
        currency=str(_required(body, "currency")),
        cash_weight=cash / total,
        positions=positions,
    )


def load_portfolio_csv(path: Path, fetched_at: str) -> PortfolioSnapshot:
    """Load the CSV fallback; a ``cash`` asset-class row is the cash balance."""
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != _CSV_HEADER:
            missing = [f for f in _CSV_HEADER if f not in (reader.fieldnames or [])]
            raise ValueError(f"missing required field: {missing[0] if missing else 'header'}")
        rows = []
        cash = 0.0
        for row in reader:
            parsed = {field: _required(row, field) for field in _CSV_HEADER}
            try:
                units = float(parsed["units"])
                open_rate = float(parsed["open_rate"])
                current_price = float(parsed["current_price"])
            except ValueError as exc:
                raise ValueError(f"invalid numeric field in CSV row: {row}") from exc
            if parsed["asset_class"] == "cash":
                cash += units * current_price
                continue
            rows.append(
                {
                    "instrument": parsed["instrument"],
                    "ticker": parsed["ticker"],
                    "asset_class": parsed["asset_class"],
                    "units": units,
                    "open_rate": open_rate,
                    "current_price": current_price,
                }
            )
    payload = {"currency": "USD", "cash": cash, "positions": rows}
    return normalize_etoro_pnl(payload, fetched_at)


def _field(record: dict, name: str):
    """Case-insensitive lookup: the API mixes ``instrumentID`` and ``instrumentId``."""
    if name in record:
        return record[name]
    lowered = name.lower()
    return next((v for k, v in record.items() if k.lower() == lowered), None)


def _public_positions(body: dict) -> tuple[list[dict], float]:
    """Direct and copy-trading positions, plus cash held inside copy mirrors."""
    positions = list(_field(body, "positions") or [])
    mirror_cash = 0.0
    for mirror in _field(body, "mirrors") or []:
        positions += list(_field(mirror, "positions") or [])
        mirror_cash += float(_field(mirror, "availableAmount") or 0.0)
    return positions, mirror_cash


def public_instrument_ids(pnl: dict) -> list[int]:
    body = pnl.get("clientPortfolio") if isinstance(pnl, dict) else None
    if not isinstance(body, dict):
        return []
    positions, _ = _public_positions(body)
    return sorted({int(_field(p, "instrumentID")) for p in positions if _field(p, "instrumentID")})


def normalize_public_api(pnl: dict, instruments: dict, fetched_at: str) -> PortfolioSnapshot:
    """Normalize the Public API PnL + instruments responses; any gap aborts by name.

    Lots of the same instrument are aggregated (units summed, entry price
    units-weighted). Short and leveraged positions abort the snapshot rather
    than being dropped, so a partial portfolio is never written.
    """
    body = pnl.get("clientPortfolio") if isinstance(pnl, dict) else None
    if not isinstance(body, dict):
        raise ValueError("missing required field: clientPortfolio")
    currency_id = _field(body, "accountCurrencyId")
    if currency_id is not None and int(currency_id) != USD_CURRENCY_ID:
        raise ValueError(f"unsupported account currency id {currency_id}; only USD is supported")
    credit = _field(body, "credit")
    if credit is None:
        raise ValueError("missing required field: credit")
    raw_positions, mirror_cash = _public_positions(body)

    meta = {}
    for item in (instruments or {}).get("instrumentDisplayDatas", []):
        meta[int(_field(item, "instrumentID"))] = item

    lots: dict[int, dict] = {}
    unsupported: list[str] = []
    for raw in raw_positions:
        instrument_id = _field(raw, "instrumentID")
        if instrument_id is None:
            raise ValueError("missing required field: instrumentID")
        instrument_id = int(instrument_id)
        info = meta.get(instrument_id)
        if info is None:
            raise ValueError(f"instrument {instrument_id} missing from the instruments lookup")
        symbol = str(_field(info, "symbolFull") or instrument_id)
        if _field(raw, "isBuy") is False:
            unsupported.append(f"{symbol} (short)")
            continue
        if float(_field(raw, "leverage") or 1) != 1:
            unsupported.append(f"{symbol} (leverage {_field(raw, 'leverage')}x)")
            continue
        type_id = _field(info, "instrumentTypeID")
        asset_class = INSTRUMENT_TYPE_CLASSES.get(int(type_id) if type_id is not None else -1)
        if asset_class is None:
            raise ValueError(
                f"{symbol}: instrument type {type_id} is not supported (stocks, ETFs, crypto only)"
            )
        pnl_detail = _field(raw, "unrealizedPnL")
        detail = pnl_detail if isinstance(pnl_detail, dict) else {}
        close = _field(detail, "closeRate") or _field(raw, "closeRate")
        if not close:
            raise ValueError(f"{symbol}: missing required field: closeRate")
        conversion = float(
            _field(detail, "closeConversionRate") or _field(raw, "closeConversionRate") or 1.0
        )
        units = float(_required(raw, "units"))
        open_rate = float(_field(raw, "openRate") or 0)
        if open_rate <= 0:
            raise ValueError(f"{symbol}: missing required field: openRate")
        lot = lots.setdefault(
            instrument_id,
            {
                "ticker": symbol,
                "instrument": str(_field(info, "instrumentDisplayName") or symbol),
                "asset_class": asset_class,
                "units": 0.0,
                "cost": 0.0,
                "value": 0.0,
                "close": float(close),
            },
        )
        lot["units"] += units
        lot["cost"] += units * open_rate
        lot["value"] += units * float(close) * conversion
    if unsupported:
        raise ValueError(
            "the eToro account holds positions the agent cannot represent: "
            + ", ".join(unsupported)
            + "; export the supported holdings and fetch with --csv instead"
        )

    cash = float(credit) + mirror_cash
    total = cash + sum(lot["value"] for lot in lots.values())
    if total <= 0:
        raise ValueError("total account value must be positive")
    positions = []
    for lot in lots.values():
        open_rate = lot["cost"] / lot["units"]
        positions.append(
            PortfolioPosition(
                instrument=lot["instrument"],
                ticker=lot["ticker"],
                asset_class=lot["asset_class"],
                units=lot["units"],
                open_rate=open_rate,
                current_price=lot["close"],
                unrealized_pnl_pct=(lot["close"] - open_rate) / open_rate,
                portfolio_weight=lot["value"] / total,
            )
        )
    return PortfolioSnapshot(
        fetched_at=fetched_at, currency="USD", cash_weight=cash / total, positions=positions
    )


class EtoroClient:
    """Read-only eToro Public API client with an injected ``get(url, headers)`` transport."""

    def __init__(
        self,
        api_key: str,
        user_key: str,
        get: Callable[[str, dict[str, str]], dict],
        environment: str = "real",
    ) -> None:
        if environment not in PNL_PATHS:
            raise ValueError(f"ETORO_ENVIRONMENT must be one of {sorted(PNL_PATHS)}")
        self.api_key, self.user_key, self.environment = api_key, user_key, environment
        self._get = get

    @classmethod
    def from_env(cls, get: Callable[[str, dict[str, str]], dict]) -> "EtoroClient":
        keys = {}
        for name in ("ETORO_API_KEY", "ETORO_USER_KEY"):
            if not os.environ.get(name):
                raise ValueError(f"{name} is not set; export it, or fetch with --csv")
            keys[name] = os.environ[name]
        return cls(
            keys["ETORO_API_KEY"],
            keys["ETORO_USER_KEY"],
            get,
            environment=os.environ.get("ETORO_ENVIRONMENT", "real"),
        )

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "x-user-key": self.user_key,
            "x-request-id": str(uuid.uuid4()),
            "Accept": "application/json",
        }

    def fetch_portfolio(self) -> PortfolioSnapshot:
        fetched_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        pnl = self._get(ETORO_API_BASE + PNL_PATHS[self.environment], self._headers())
        ids = public_instrument_ids(pnl)
        instruments = {"instrumentDisplayDatas": []}
        if ids:
            query = ",".join(str(i) for i in ids)
            instruments = self._get(
                f"{ETORO_API_BASE}{INSTRUMENTS_PATH}?instrumentIds={query}", self._headers()
            )
        return normalize_public_api(pnl, instruments, fetched_at)
