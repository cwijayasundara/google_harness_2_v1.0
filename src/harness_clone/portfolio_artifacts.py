"""Deterministic validation of portfolio recommendation artifacts."""

import json
from collections.abc import Iterable, Mapping

from .contracts import Recommendation

BENCHMARK_CLASSES: dict[str, str] = {"SPY": "etf", "QQQ": "etf", "BTC": "crypto", "ETH": "crypto"}
BENCHMARK_TICKERS = frozenset(BENCHMARK_CLASSES)
CRYPTO_BENCHMARKS = frozenset({"BTC", "ETH"})
EQUITY_BENCHMARKS = frozenset({"SPY", "QQQ"})
MAX_TARGET_WEIGHT_PCT = 20.0
MAX_NON_HOLD_CALLS = 5
HORIZON_DAYS = 7


def _ticker_classes(
    allowed: Iterable[str] | Mapping[str, str | Iterable[str]],
) -> dict[str, set[str] | None]:
    """Map each allowed ticker to its snapshot classes (``None``: class unknown)."""
    classes: dict[str, set[str] | None] = {}
    if isinstance(allowed, Mapping):
        for ticker, value in allowed.items():
            values = {value} if isinstance(value, str) else set(value)
            classes[str(ticker)] = {v.lower() for v in values}
    else:
        for ticker in allowed:
            classes[str(ticker)] = None
    for ticker, asset_class in BENCHMARK_CLASSES.items():
        known = classes.get(ticker) or set()
        classes[ticker] = known | {asset_class}
    return classes


def validate_recommendations(
    text: str, allowed_tickers: Iterable[str] | Mapping[str, str | Iterable[str]]
) -> list[Recommendation]:
    """Parse and validate a recommendations JSON list.

    Tickers must be in ``allowed_tickers`` (holdings union candidates)
    plus the benchmark tickers SPY, QQQ, BTC, ETH. Every record must have
    ``target_weight_pct`` at most 20 and ``horizon_days`` equal to 7, at
    most 5 non-hold calls are allowed, and every non-hold call must carry a
    non-empty thesis, invalidation, and evidence_refs.

    Scoring-relevant fields are pinned to the snapshot so an evolving
    bundle cannot flatter its own score: ``benchmark`` must be SPY/QQQ for
    non-crypto calls and BTC/ETH for crypto calls, and when
    ``allowed_tickers`` maps ticker to snapshot asset class (benchmarks
    always do) ``asset_class`` must match it. Violations raise
    ``ValueError`` naming the rule.
    """
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"recommendations must be valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("recommendations must be a JSON list")
    try:
        recs = [Recommendation.model_validate(item) for item in data]
    except ValueError as exc:
        raise ValueError(f"invalid recommendation record: {exc}") from exc

    allowed = _ticker_classes(allowed_tickers)
    non_hold = 0
    for rec in recs:
        if rec.ticker not in allowed:
            raise ValueError(f"unknown ticker {rec.ticker!r}: ticker not in allowed tickers")
        snapshot_classes = allowed[rec.ticker]
        asset_class = rec.asset_class.lower()
        if snapshot_classes is not None and asset_class not in snapshot_classes:
            raise ValueError(
                f"asset_class {rec.asset_class!r} for {rec.ticker} does not match the "
                f"snapshot asset_class {sorted(snapshot_classes)}"
            )
        if rec.horizon_days != HORIZON_DAYS:
            raise ValueError(
                f"horizon_days {rec.horizon_days} for {rec.ticker} must be {HORIZON_DAYS}"
            )
        expected = CRYPTO_BENCHMARKS if asset_class == "crypto" else EQUITY_BENCHMARKS
        if rec.benchmark not in expected:
            raise ValueError(
                f"benchmark {rec.benchmark!r} for {rec.ticker} ({asset_class}) must be one of "
                f"{sorted(expected)}"
            )
        if rec.target_weight_pct > MAX_TARGET_WEIGHT_PCT:
            raise ValueError(
                f"target_weight_pct {rec.target_weight_pct} for {rec.ticker} "
                "exceeds max 20% single-position target weight"
            )
        if rec.action != "hold":
            non_hold += 1
            if not rec.thesis.strip():
                raise ValueError(f"non-hold call for {rec.ticker} requires a non-empty thesis")
            if not rec.invalidation.strip():
                raise ValueError(
                    f"non-hold call for {rec.ticker} requires a non-empty invalidation"
                )
            if not rec.evidence_refs or not any(ref.strip() for ref in rec.evidence_refs):
                raise ValueError(
                    f"non-hold call for {rec.ticker} requires non-empty evidence_refs"
                )
    if non_hold > MAX_NON_HOLD_CALLS:
        raise ValueError(
            f"too many non-hold calls: {non_hold} exceeds max 5 non-hold calls per day"
        )
    return recs
