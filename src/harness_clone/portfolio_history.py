"""Point-in-time historical bootstrap snapshots (spec section 7.2).

One frozen snapshot task per historical day, rebuilt using only closes
ending on or before that day. The portfolio written into each snapshot is
the current holdings passed in as ``proxy_portfolio``, serialized with
``"proxy": true``: real historical holdings are unknowable, so no consumer
may mistake it for the historical account. Calls made on these snapshots
are scored; the proxy portfolio itself is not.

Callers split ``days`` with :func:`harness_clone.portfolio_score.split_windows`
(evolvable window vs. holdout) and pass each slice here; task order follows
``days`` order, so the split stays aligned with the returned tasks.
"""

from datetime import date
from pathlib import Path

from .contracts import PortfolioSnapshot, TaskSpec
from .portfolio_market import COIN_IDS, PriceProvider, market_summary, screen_candidates
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


def build_history(
    base: Path,
    days: list[date],
    tickers: list[str],
    provider: PriceProvider,
    proxy_portfolio: PortfolioSnapshot,
) -> list[TaskSpec]:
    """Build one point-in-time snapshot task per day in ``days``.

    Only closes ending on or before each day enter that day's snapshot:
    every series is requested with ``end=day``, and a provider returning
    more closes than the requested bounded window cannot be shown to end
    by that day, so it raises ``ValueError`` before any task is written.
    """
    unique_tickers = list(dict.fromkeys(tickers))
    tasks: list[TaskSpec] = []
    for day in days:
        summaries = {}
        for ticker in unique_tickers:
            asset_class = _asset_class(ticker, proxy_portfolio)
            closes = provider.closes(ticker, asset_class, day, HISTORY_DAYS)
            if len(closes) > HISTORY_DAYS:
                raise ValueError(
                    f"closes for {ticker} exceed the {HISTORY_DAYS}-day window "
                    f"ending {day.isoformat()}; series end cannot be verified "
                    "as on or before that day"
                )
            summaries[ticker] = market_summary(ticker, asset_class, list(closes))
        candidates = screen_candidates(list(summaries.values()))
        tasks.append(
            build_snapshot_task(
                base,
                day,
                proxy_portfolio,
                summaries,
                candidates,
                len(unique_tickers),
                [],
                proxy=True,
            )
        )
    return tasks
