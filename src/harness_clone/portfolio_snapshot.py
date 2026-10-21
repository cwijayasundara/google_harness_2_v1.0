"""Build frozen, dated portfolio snapshot tasks."""

import json
from datetime import date
from pathlib import Path

from .contracts import Candidate, MarketSummary, PortfolioSnapshot, TaskSpec
from .tasks import validate_task

MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 2_000_000

_PROMPT = """\
You are analysing a frozen daily portfolio snapshot dated {day} \
(snapshot fetched at {fetched_at}). Read every file in the source \
directory before making any call: `portfolio.json` (holdings, cash \
weight, currency), `market.json` (per-ticker market summaries), \
`candidates.json` (screened candidates, with `universe_considered` and \
`universe_returned` coverage counts and `universe_missing` — tickers with \
no market data, which the report must mention), and `prior_outcomes.json` \
(matured outcomes of past recommendations; empty when there are none).

Produce exactly two outputs:

1. `recommendations.json` — a JSON list of records, one per call, each \
with exactly these fields:
   - `ticker` (string)
   - `asset_class` (string; must equal the ticker's `asset_class` in the snapshot)
   - `action`: one of `hold|trim|add|new_idea|exit`
   - `target_weight_pct` (number, percent of portfolio)
   - `horizon_days` (integer; must be 7)
   - `thesis` (string)
   - `invalidation` (string)
   - `benchmark` (string: `SPY` or `QQQ` for equity/etf calls, `BTC` or `ETH` for \
crypto calls)
   - `evidence_refs` (list of strings citing source evidence, e.g. \
`market.json:AAPL.30d_return`)
   Example record: {{"ticker": "AAPL", "asset_class": "equity", \
"action": "hold", "target_weight_pct": 8.0, "horizon_days": 7, \
"thesis": "...", "invalidation": "...", "benchmark": "SPY", \
"evidence_refs": ["market.json:AAPL.30d_return"]}}

2. `report.md` — a human-readable report that leads with \
portfolio-level risk, then gives the per-call rationale for each \
recommendation, includes the research-only notice (these are research \
recommendations for the account holder's manual review; no orders are \
placed), and states the snapshot fetch timestamp. The host stamps the \
bundle digest and snapshot age onto the delivered report as provenance.

Base every call only on the snapshot evidence provided; do not invent \
prices, holdings, or outcomes that are not in the source files.
"""


def build_snapshot_task(
    base: Path,
    day: date,
    portfolio: PortfolioSnapshot,
    summaries: dict[str, MarketSummary],
    candidates: list[Candidate],
    universe_considered: int,
    prior_outcomes: list[dict],
    *,
    proxy: bool = False,
    portfolio_extra: dict | None = None,
    universe_missing: dict | None = None,
) -> TaskSpec:
    dated_dir = Path(base) / day.isoformat()
    if dated_dir.exists() or dated_dir.is_symlink():
        raise ValueError(f"snapshot already exists: {dated_dir}")
    source_root = (dated_dir / "source").resolve()

    task = TaskSpec(
        id=f"portfolio-{day.strftime('%Y%m%d')}",
        domain="portfolio",
        prompt=_PROMPT.format(day=day.isoformat(), fetched_at=portfolio.fetched_at),
        source_root=source_root,
        output_paths=["recommendations.json", "report.md"],
        ordinal=int(day.strftime("%Y%m%d")),
    )

    task_data = task.model_dump(mode="json")
    task_data["source_root"] = "source"
    portfolio_data = portfolio.model_dump(mode="json")
    if proxy:
        # Serialization-only flag: the caller's model object is never mutated.
        portfolio_data["proxy"] = True
    if portfolio_extra:
        portfolio_data.update(portfolio_extra)
    payloads: dict[str, bytes] = {
        "task.json": json.dumps(task_data, indent=2).encode(),
        "source/portfolio.json": json.dumps(portfolio_data, indent=2).encode(),
        "source/market.json": json.dumps(
            {ticker: s.model_dump(mode="json") for ticker, s in summaries.items()},
            indent=2,
        ).encode(),
        "source/candidates.json": json.dumps(
            {
                "universe_considered": universe_considered,
                "universe_returned": len(candidates),
                **({"universe_missing": universe_missing} if universe_missing is not None else {}),
                "candidates": [c.model_dump(mode="json") for c in candidates],
            },
            indent=2,
        ).encode(),
        "source/prior_outcomes.json": json.dumps(prior_outcomes, indent=2).encode(),
    }

    total = 0
    for rel, data in payloads.items():
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"snapshot file exceeds 1MB: {rel}")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("snapshot evidence exceeds 2MB total")

    (dated_dir / "source").mkdir(parents=True)
    for rel, data in payloads.items():
        (dated_dir / rel).write_bytes(data)
    return validate_task(task)
