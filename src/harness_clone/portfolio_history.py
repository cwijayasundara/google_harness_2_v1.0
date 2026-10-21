"""Point-in-time historical bootstrap snapshots (spec section 7.2).

One frozen snapshot task per historical day, rebuilt using only closes
ending on or before that day. The portfolio written into each snapshot is
the current holdings passed in as ``proxy_portfolio``, serialized with
``"proxy": true``: real historical holdings are unknowable, so no consumer
may mistake it for the historical account. Calls made on these snapshots
are scored; the proxy portfolio itself is not.

Only tickers, units, and the cash weight carry over from the proxy. Every
price-derived field is rebuilt from that day's close so no future price
leaks into a past-dated task: ``current_price`` and ``open_rate`` are the
day's ``last_price`` (proxy positions are treated as opened that day, so
``unrealized_pnl_pct`` is 0), weights are recomputed from day prices over
the proxy's non-cash share, and ``fetched_at`` is the day itself. A holding
with no close that day is dropped and listed in ``missing_positions``.

Callers split ``days`` with :func:`harness_clone.portfolio_score.split_windows`
(evolvable window vs. holdout) and pass each slice here; task order follows
``days`` order, so the split stays aligned with the returned tasks.
"""

from datetime import date
from pathlib import Path

from .contracts import MarketSummary, PortfolioSnapshot, TaskSpec
from .portfolio_market import COIN_IDS, DatedPriceProvider, market_summary, screen_candidates
from .portfolio_snapshot import build_snapshot_task

# Matches the live fetch window in portfolio_ops so historical summaries
# are computed over the same lookback as live snapshot summaries.
HISTORY_DAYS = 365

_BENCHMARK_CLASSES = {"SPY": "etf", "QQQ": "etf"}


def _asset_class(ticker: str, proxy_portfolio: PortfolioSnapshot) -> str:
    for position in proxy_portfolio.positions:
        if position.ticker == ticker:
            return position.asset_class
    upper = ticker.upper()
    if upper in COIN_IDS:
        return "crypto"
    return _BENCHMARK_CLASSES.get(upper, "equity")


def _reprice(
    proxy: PortfolioSnapshot, summaries: dict[str, MarketSummary], day: date
) -> tuple[PortfolioSnapshot, list[str]]:
    priced = []
    missing = []
    for position in proxy.positions:
        summary = summaries.get(position.ticker)
        if summary is None or summary.missing or summary.last_price <= 0:
            missing.append(position.ticker)
            continue
        priced.append((position, float(summary.last_price)))
    total = sum(position.units * price for position, price in priced)
    invested = 1.0 - proxy.cash_weight
    positions = [
        position.model_copy(
            update={
                "current_price": price,
                "open_rate": price,
                "unrealized_pnl_pct": 0.0,
                "portfolio_weight": invested * position.units * price / total if total else 0.0,
            }
        )
        for position, price in priced
    ]
    snapshot = PortfolioSnapshot(
        fetched_at=f"{day.isoformat()}T23:59:59Z",
        currency=proxy.currency,
        cash_weight=proxy.cash_weight,
        positions=positions,
    )
    return snapshot, missing


def build_history(
    base: Path,
    days: list[date],
    tickers: list[str],
    provider: DatedPriceProvider,
    proxy_portfolio: PortfolioSnapshot,
) -> list[TaskSpec]:
    """Build one point-in-time snapshot task per day in ``days``.

    Only closes dated on or before each day enter that day's snapshot:
    every series is requested with ``end=day`` as dated closes, and a
    provider returning any close dated after that day raises
    ``ValueError`` before any task is written.
    """
    holdings = [position.ticker for position in proxy_portfolio.positions]
    unique_tickers = list(dict.fromkeys([*tickers, *holdings]))
    tasks: list[TaskSpec] = []
    for day in days:
        summaries = {}
        for ticker in unique_tickers:
            asset_class = _asset_class(ticker, proxy_portfolio)
            dated = provider.dated_closes(ticker, asset_class, day, HISTORY_DAYS)
            late = [d for d, _ in dated if d > day]
            if late:
                raise ValueError(
                    f"closes for {ticker} include {max(late).isoformat()}, after the "
                    f"snapshot day {day.isoformat()}; refusing look-ahead data"
                )
            closes = [value for _, value in sorted(dated)]
            summaries[ticker] = market_summary(ticker, asset_class, closes)
        candidates = screen_candidates(list(summaries.values()))
        portfolio, missing = _reprice(proxy_portfolio, summaries, day)
        tasks.append(
            build_snapshot_task(
                base,
                day,
                portfolio,
                summaries,
                candidates,
                len(unique_tickers),
                [],
                proxy=True,
                portfolio_extra={"missing_positions": missing},
            )
        )
    return tasks
