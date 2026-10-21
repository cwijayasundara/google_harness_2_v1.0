"""Seed a synthetic portfolio base directory so the cockpit UI works offline.

Every number here is made up. The directory carries a ``.demo`` marker, the
UI labels it as demo data, and re-seeding only ever replaces a directory
that has that marker.
"""

import argparse
import json
import math
import random
import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from harness_clone.contracts import Candidate, MarketSummary, PortfolioPosition, PortfolioSnapshot
from harness_clone.portfolio_snapshot import build_snapshot_task

HOLDINGS = [
    ("Apple Inc", "AAPL", "equity", 40, 172.0, 228.0),
    ("Microsoft Corp", "MSFT", "equity", 18, 330.0, 418.0),
    ("NVIDIA Corp", "NVDA", "equity", 30, 96.0, 138.0),
    ("Vanguard S&P 500 ETF", "VOO", "etf", 12, 420.0, 512.0),
    ("Tesla Inc", "TSLA", "equity", 14, 260.0, 214.0),
    ("Bitcoin", "BTC", "crypto", 0.06, 52000.0, 67000.0),
    ("Ethereum", "ETH", "crypto", 0.9, 3100.0, 2600.0),
    ("Intel Corp", "INTC", "equity", 120, 34.0, 21.5),
]
CANDIDATES = [("AMD", "equity"), ("AVGO", "equity"), ("SOL", "crypto"), ("COST", "equity")]
CASH = 2400.0


def _day_list(end: date, count: int) -> list[date]:
    days, day = [], end
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


def _positions(rng: random.Random, drift: float) -> tuple[list[PortfolioPosition], float]:
    rows = []
    for name, ticker, cls, units, open_rate, price in HOLDINGS:
        price = round(price * (1 + drift * rng.uniform(-1, 1)), 2)
        rows.append((name, ticker, cls, units, open_rate, price))
    total = sum(u * p for *_, u, _o, p in rows) + CASH
    positions = [
        PortfolioPosition(
            instrument=name,
            ticker=ticker,
            asset_class=cls,
            units=units,
            open_rate=open_rate,
            current_price=price,
            unrealized_pnl_pct=(price - open_rate) / open_rate,
            portfolio_weight=units * price / total,
        )
        for name, ticker, cls, units, open_rate, price in rows
    ]
    return positions, CASH / total


def _summary(rng: random.Random, ticker: str, cls: str, price: float) -> MarketSummary:
    vol = 0.55 if cls == "crypto" else rng.uniform(0.18, 0.45)
    return MarketSummary(
        ticker=ticker,
        asset_class=cls,
        last_price=price,
        return_7d_pct=round(rng.gauss(0.4, 3.5), 2),
        return_30d_pct=round(rng.gauss(1.5, 8.0), 2),
        volatility_30d=round(vol, 3),
        high_52w_distance_pct=round(-abs(rng.gauss(8, 9)), 2),
    )


def _recommendations(positions, market, candidates) -> list[dict]:
    by_ticker = {p.ticker: p for p in positions}
    calls = []

    def call(ticker, cls, action, target, thesis, invalidation, refs, bench):
        calls.append(
            {
                "ticker": ticker,
                "asset_class": cls,
                "action": action,
                "target_weight_pct": round(target, 1),
                "horizon_days": 7,
                "thesis": thesis,
                "invalidation": invalidation,
                "benchmark": bench,
                "evidence_refs": refs,
            }
        )

    aapl, voo = by_ticker["AAPL"], by_ticker["VOO"]
    trimmed = 15.0
    call(
        "AAPL",
        "equity",
        "trim",
        trimmed,
        "Position has grown well past its sizing band on a strong run; locking in part of the "
        "gain keeps single-name risk under control.",
        "A fresh 52-week high with improving breadth would argue for letting it run.",
        ["portfolio.json:AAPL.portfolio_weight", "market.json:AAPL.return_30d_pct"],
        "QQQ",
    )
    call(
        "INTC",
        "equity",
        "exit",
        0.0,
        f"Down {abs(by_ticker['INTC'].unrealized_pnl_pct) * 100:.0f}% from entry with weak "
        "30-day momentum and no catalyst in the evidence; capital is better used elsewhere.",
        "A reclaim of the 30-day trend with falling volatility.",
        [
            "portfolio.json:INTC.unrealized_pnl_pct",
            "market.json:INTC.return_30d_pct",
            "market.json:INTC.volatility_30d",
        ],
        "SPY",
    )
    call(
        "VOO",
        "etf",
        "add",
        min(20.0, (voo.portfolio_weight + aapl.portfolio_weight) * 100 - trimmed),
        "Rotating trimmed single-name exposure into the broad index lowers concentration "
        "without leaving the market.",
        "Index breaks below its 30-day low.",
        ["market.json:VOO.volatility_30d", "portfolio.json:VOO.portfolio_weight"],
        "SPY",
    )
    top = candidates[0]
    call(
        top.ticker,
        top.asset_class,
        "new_idea",
        3.0,
        f"Highest-scoring screened candidate ({top.score:.2f}) with momentum leading its peers; "
        "a small starter position.",
        "Momentum score falls out of the top quartile.",
        [f"candidates.json:{top.ticker}.score"],
        "BTC" if top.asset_class == "crypto" else "QQQ",
    )
    for p in positions:
        if p.ticker in {"AAPL", "INTC", "VOO"}:
            continue
        bench = "BTC" if p.asset_class == "crypto" else "SPY"
        call(
            p.ticker,
            p.asset_class,
            "hold",
            p.portfolio_weight * 100,
            "Within its sizing band.",
            "",
            [f"market.json:{p.ticker}.return_7d_pct"],
            bench,
        )
    return calls


def _report(day: date, recs: list[dict]) -> str:
    lines = [
        f"# Portfolio review — {day.isoformat()}",
        "",
        "> Research recommendations for the account holder's manual review. "
        "**No orders are placed.** Synthetic demo data.",
        "",
        "## Portfolio-level risk",
        "",
        "Concentration is the main risk: the top two single names carry roughly a third of the "
        "portfolio, and crypto adds a high-volatility sleeve. Cash is a modest buffer.",
        "",
        "## Calls",
        "",
    ]
    for r in recs:
        if r["action"] == "hold":
            continue
        lines += [
            f"### {r['action'].upper()} {r['ticker']} → {r['target_weight_pct']}%",
            "",
            r["thesis"],
            "",
            f"*Invalidation:* {r['invalidation']}",
            "",
        ]
    return "\n".join(lines)


def _evolve_run(base: Path, week: str, digest: str, executions: int, delivered: int) -> None:
    run = base / "runs" / f"evolve-{week}"
    run.mkdir(parents=True)
    with sqlite3.connect(run / "run.sqlite") as conn:
        conn.execute("create table records (kind text, id text, value text, primary key(kind,id))")
        conn.execute(
            "insert into records values ('stream','signature',?)", (json.dumps({"base": digest}),)
        )
        for i in range(executions):
            conn.execute(
                "insert into records values ('execution',?,?)",
                (f"demo/{week}/{i}", json.dumps({"delivered": i < delivered})),
            )


def seed(base: Path, end: date, days: int = 8, seed_value: int = 7) -> Path:
    base = Path(base)
    if base.exists():
        if not (base / ".demo").is_file():
            raise SystemExit(f"{base} exists and is not a demo directory; refusing to replace it")
        shutil.rmtree(base)
    base.mkdir(parents=True)
    (base / ".demo").write_text("synthetic demo data\n")
    rng = random.Random(seed_value)
    calls_board = []
    snapshot_days = _day_list(end, days)
    for i, day in enumerate(snapshot_days):
        positions, cash_weight = _positions(rng, drift=0.02 * math.sqrt(i + 1))
        snap = PortfolioSnapshot(
            fetched_at=f"{day.isoformat()}T13:30:00Z",
            currency="USD",
            cash_weight=cash_weight,
            positions=positions,
        )
        market = {
            p.ticker: _summary(rng, p.ticker, p.asset_class, p.current_price) for p in positions
        }
        cands = sorted(
            (
                Candidate(
                    ticker=t,
                    asset_class=c,
                    score=round(rng.uniform(0.4, 0.95), 2),
                    metrics={
                        "momentum_30d": round(rng.gauss(4, 6), 2),
                        "volatility_30d": round(rng.uniform(0.2, 0.7), 2),
                    },
                )
                for t, c in CANDIDATES
            ),
            key=lambda c: -c.score,
        )
        for c in cands:
            market[c.ticker] = _summary(
                rng, c.ticker, c.asset_class, round(rng.uniform(80, 400), 2)
            )
        build_snapshot_task(base, day, snap, market, cands, 500, [], coin_ids={"BTC": "bitcoin"})
        recs = _recommendations(positions, market, cands)
        (base / day.isoformat() / "recommendations.json").write_text(json.dumps(recs, indent=2))
        (base / day.isoformat() / "report.md").write_text(_report(day, recs))
        for r in recs:
            matured = day + timedelta(days=7) <= end
            calls_board.append(
                {
                    "task_date": day.isoformat(),
                    "maturity_date": (day + timedelta(days=7)).isoformat(),
                    "ticker": r["ticker"],
                    "action": r["action"],
                    "benchmark": r["benchmark"],
                    "excess_return_pct": round(rng.gauss(0.6, 2.8), 2) if matured else None,
                    "risk_adjusted": round(rng.gauss(0.2, 0.8), 2) if matured else None,
                    "status": "matured" if matured else "pending",
                }
            )
    active = [c for c in calls_board if c["status"] == "matured" and c["action"] != "hold"]
    excess = [c["excess_return_pct"] for c in active]
    (base / "scoreboard.json").write_text(
        json.dumps(
            {
                "generated_at": end.isoformat(),
                "matured": sum(c["status"] == "matured" for c in calls_board),
                "pending": sum(c["status"] == "pending" for c in calls_board),
                "unscored": 0,
                "skipped": 0,
                "mean_excess_return_pct": sum(excess) / len(excess) if excess else None,
                "hit_rate": sum(e > 0 for e in excess) / len(excess) if excess else None,
                "mean_risk_adjusted": None,
                "active_calls": len(active),
                "calls": calls_board,
            },
            indent=2,
        )
    )
    first, second = "a" * 64, "b" * 64
    weeks = sorted({f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}" for d in snapshot_days})
    for n, week in enumerate(weeks):
        _evolve_run(base, week, first if n == 0 else second, executions=4, delivered=4 - n % 2)
    (base / "bundles" / first).mkdir(parents=True)
    (base / "bundles" / second).mkdir(parents=True)
    (base / "adopted.json").write_text(json.dumps({"bundle_digest": second}, indent=2))
    return base


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-dir", type=Path, default=Path("runs/portfolio-demo"))
    p.add_argument("--end", type=date.fromisoformat, default=date.today())
    args = p.parse_args()
    print(f"seeded synthetic demo data in {seed(args.base_dir, args.end)}")
