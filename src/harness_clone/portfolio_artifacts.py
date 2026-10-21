"""Deterministic validation of portfolio recommendation artifacts."""

import json

from .contracts import Recommendation

BENCHMARK_TICKERS = frozenset({"SPY", "QQQ", "BTC", "ETH"})
MAX_TARGET_WEIGHT_PCT = 20.0
MAX_NON_HOLD_CALLS = 5


def validate_recommendations(text: str, allowed_tickers: set[str]) -> list[Recommendation]:
    """Parse and validate a recommendations JSON list.

    Tickers must be in ``allowed_tickers`` (holdings union candidates)
    plus the benchmark tickers SPY, QQQ, BTC, ETH. Every record must have
    ``target_weight_pct`` at most 20, at most 5 non-hold calls are allowed,
    and every non-hold call must carry a non-empty thesis, invalidation,
    and evidence_refs. Violations raise ``ValueError`` naming the rule.
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

    allowed = set(allowed_tickers) | BENCHMARK_TICKERS
    non_hold = 0
    for rec in recs:
        if rec.ticker not in allowed:
            raise ValueError(f"unknown ticker {rec.ticker!r}: ticker not in allowed tickers")
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
