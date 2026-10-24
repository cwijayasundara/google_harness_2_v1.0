"""Pure portfolio math for the agent to call in-container. No I/O."""

import statistics


def _value(position: dict) -> float:
    if "units" in position and "current_price" in position:
        return float(position["units"]) * float(position["current_price"])
    for key in ("market_value", "value", "portfolio_weight"):
        if key in position:
            return float(position[key])
    return 0.0


def weights(positions: list[dict]) -> dict[str, float]:
    """Portfolio weight per ticker from market values; sums to 1.0."""
    values: dict[str, float] = {}
    for position in positions:
        ticker = position.get("ticker")
        if not ticker:
            continue
        values[str(ticker)] = values.get(str(ticker), 0.0) + _value(position)
    total = sum(values.values())
    if total <= 0:
        return {}
    return {ticker: value / total for ticker, value in values.items()}


def drift(current: dict, target: dict) -> dict[str, float]:
    """Per-ticker drift (current weight minus target weight) over the union."""
    keys = set(current) | set(target)
    return {key: float(current.get(key, 0.0)) - float(target.get(key, 0.0)) for key in keys}


def volatility(closes: list[float]) -> float:
    """Population stdev of daily percentage returns, as in portfolio_market."""
    if len(closes) < 2:
        return 0.0
    returns = [
        (curr / prev - 1.0) * 100.0
        for prev, curr in zip(closes, closes[1:], strict=False)
        if prev != 0
    ]
    if len(returns) < 2:
        return 0.0
    return statistics.pstdev(returns)
