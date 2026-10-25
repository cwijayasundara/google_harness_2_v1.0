"""CSV portfolio loading, normalized to PortfolioSnapshot."""

import csv
from pathlib import Path

from harness_clone.contracts import PortfolioPosition, PortfolioSnapshot

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


def normalize_portfolio(payload: dict, fetched_at: str) -> PortfolioSnapshot:
    """Normalize a ticker-keyed portfolio payload; missing fields abort by name."""
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
    """Load a holdings CSV; a ``cash`` asset-class row is the cash balance."""
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
    return normalize_portfolio(payload, fetched_at)
