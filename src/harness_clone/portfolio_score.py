"""Offline forward scorer, causal outcomes feed, and backtest window split."""

import json
import re
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from .contracts import Recommendation, Record, TaskResult
from .storage import RunStore

PriceFn = Callable[[str, str, date, date], tuple[float, float] | None]

MIN_MATURED_ACTIVE = 20


class ScoredCall(Record):
    task_date: date
    maturity_date: date
    ticker: str
    action: str
    benchmark: str
    excess_return_pct: float | None = None
    risk_adjusted: float | None = None
    status: Literal["matured", "pending", "unscored"]


def _cost_pct(rec: Recommendation) -> float:
    if rec.action == "hold":
        return 0.0
    return 0.25 if rec.asset_class.lower() == "crypto" else 0.10


def score_call(
    rec: Recommendation,
    task_date: date,
    today: date,
    prices: PriceFn,
    volatility: float,
) -> ScoredCall:
    maturity_date = task_date + timedelta(days=rec.horizon_days)
    base = {
        "task_date": task_date,
        "maturity_date": maturity_date,
        "ticker": rec.ticker,
        "action": rec.action,
        "benchmark": rec.benchmark,
    }
    if today < maturity_date:
        return ScoredCall(**base, status="pending")
    ticker_prices = prices(rec.ticker, rec.asset_class, task_date, maturity_date)
    benchmark_class = "crypto" if rec.benchmark.upper() in {"BTC", "ETH"} else "equity"
    bench_prices = prices(rec.benchmark, benchmark_class, task_date, maturity_date)
    if (
        ticker_prices is None
        or bench_prices is None
        or ticker_prices[0] == 0
        or bench_prices[0] == 0
    ):
        return ScoredCall(**base, status="unscored")
    ticker_ret = (ticker_prices[1] / ticker_prices[0] - 1.0) * 100.0
    bench_ret = (bench_prices[1] / bench_prices[0] - 1.0) * 100.0
    excess = ticker_ret - bench_ret - _cost_pct(rec)
    risk = excess / volatility if volatility else None
    return ScoredCall(**base, excess_return_pct=excess, risk_adjusted=risk, status="matured")


def _task_date(task_id: str, tasks_by_id: dict[str, dict]) -> date | None:
    task = tasks_by_id.get(task_id)
    if task:
        ordinal = task.get("ordinal")
        if ordinal:
            try:
                return date(int(str(ordinal)[:4]), int(str(ordinal)[4:6]), int(str(ordinal)[6:8]))
            except (ValueError, IndexError):
                pass
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", task_id)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"(\d{8})", task_id)
    if m:
        digits = m.group(1)
        try:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError:
            return None
    return None


def scoreboard(
    run_root: Path,
    out: Path,
    today: date,
    prices: PriceFn,
    volatilities: dict[str, float],
) -> dict:
    run_root = Path(run_root)
    out = Path(out)
    resolved_out = out.resolve()
    if resolved_out.is_relative_to(run_root.resolve()):
        raise ValueError("scoreboard output must be outside the online run directory")

    store = RunStore(run_root)
    results = [TaskResult.model_validate(r) for r in store.get("runtime", "results") or []]
    tasks_by_id = {
        t["id"]: t
        for t in (store.get("runtime", "tasks") or [])
        if isinstance(t, dict) and "id" in t
    }

    calls: list[ScoredCall] = []
    skipped = 0
    for result in results:
        chosen = next(
            (
                r
                for r in result.executions.values()
                if r.execution_id == result.selection.chosen_execution_id
            ),
            None,
        )
        if chosen is None or not chosen.delivered:
            skipped += 1
            continue
        artifact = chosen.artifacts.get("recommendations.json")
        if artifact is None:
            skipped += 1
            continue
        task_date = _task_date(result.task_id, tasks_by_id)
        if task_date is None:
            skipped += 1
            continue
        try:
            raw = json.loads(artifact)
            recs = [Recommendation.model_validate(item) for item in raw]
        except (ValueError, TypeError):
            skipped += 1
            continue
        for rec in recs:
            calls.append(
                score_call(rec, task_date, today, prices, volatilities.get(rec.ticker, 0.0))
            )

    matured = sum(c.status == "matured" for c in calls)
    pending = sum(c.status == "pending" for c in calls)
    unscored = sum(c.status == "unscored" for c in calls)
    active = [c for c in calls if c.status == "matured" and c.action != "hold"]
    active_calls = len(active)

    if active_calls >= MIN_MATURED_ACTIVE:
        excesses = [c.excess_return_pct for c in active if c.excess_return_pct is not None]
        risks = [c.risk_adjusted for c in active if c.risk_adjusted is not None]
        mean_excess = sum(excesses) / len(excesses) if excesses else None
        hit_rate = sum(1 for e in excesses if e > 0) / len(excesses) if excesses else None
        mean_risk = sum(risks) / len(risks) if risks else None
    else:
        mean_excess = hit_rate = mean_risk = None

    report = {
        "generated_at": today.isoformat(),
        "matured": matured,
        "pending": pending,
        "unscored": unscored,
        "skipped": skipped,
        "mean_excess_return_pct": mean_excess,
        "hit_rate": hit_rate,
        "mean_risk_adjusted": mean_risk,
        "active_calls": active_calls,
        "calls": [c.model_dump(mode="json") for c in calls],
    }
    resolved_out.parent.mkdir(parents=True, exist_ok=True)
    resolved_out.write_text(json.dumps(report, indent=2))
    return report


def outcomes_for(scoreboard_path: Path, task_date: date) -> list[dict]:
    data = json.loads(Path(scoreboard_path).read_text())
    calls = data.get("calls", []) if isinstance(data, dict) else data
    out: list[dict] = []
    for call in calls:
        if call.get("status") != "matured":
            continue
        try:
            maturity = date.fromisoformat(str(call["maturity_date"]))
        except (KeyError, ValueError):
            continue
        if maturity < task_date:
            out.append(call)
    return out


def split_windows(days: list[date], holdout: int = 1) -> tuple[list[date], list[date]]:
    days = list(days)
    if holdout <= 0:
        return days, []
    if holdout >= len(days):
        return [], days
    return days[:-holdout], days[-holdout:]
