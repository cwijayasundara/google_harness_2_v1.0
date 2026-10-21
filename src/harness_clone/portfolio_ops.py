"""Portfolio operations: snapshot fetching, daily execution, weekly evolution.

Executor and store are injected so tests run with fakes and no Docker,
network, or model. Host-side market/portfolio fetching never enters a
candidate bundle.
"""

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

from .contracts import ExecutionRecord, ExperimentConfig, PortfolioSnapshot, TaskSpec
from .experience import run_stream
from .portfolio_artifacts import validate_recommendations
from .portfolio_market import (
    PriceProvider,
    market_summary,
    screen_candidates,
    sp500_tickers,
    top_crypto_symbols,
)
from .portfolio_score import outcomes_for
from .portfolio_snapshot import build_snapshot_task
from .storage import RunStore

BENCHMARKS: dict[str, str] = {"SPY": "etf", "QQQ": "etf", "BTC": "crypto", "ETH": "crypto"}
HISTORY_DAYS = 365
ADOPTED_FILE = "adopted.json"


def _default_sp500_fetch(url: str) -> str:
    import httpx

    response = httpx.get(url, timeout=30.0)
    response.raise_for_status()
    return response.text


def _default_crypto_get(url: str, params: dict):
    import httpx

    response = httpx.get(url, params=params, timeout=30.0)
    response.raise_for_status()
    return response.json()


class RoutedPriceProvider:
    """Dispatch closes by asset class: equities/ETFs vs crypto."""

    def __init__(self, equity_provider: PriceProvider, crypto_provider: PriceProvider) -> None:
        self._equity = equity_provider
        self._crypto = crypto_provider

    def closes(self, ticker: str, asset_class: str, end: date, days: int) -> list[float]:
        provider = self._crypto if asset_class == "crypto" else self._equity
        return provider.closes(ticker, asset_class, end, days)


def live_price_provider() -> RoutedPriceProvider:
    from .portfolio_market import CoinGeckoProvider, YFinanceProvider

    return RoutedPriceProvider(YFinanceProvider(), CoinGeckoProvider())


def _snapshot_tickers(task: TaskSpec) -> dict[str, set[str]]:
    """Ticker -> snapshot asset classes over holdings and candidates."""
    source = Path(task.source_root)
    try:
        portfolio = json.loads((source / "portfolio.json").read_text())
        candidates = json.loads((source / "candidates.json").read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read snapshot tickers for {task.id}: {exc}") from exc
    tickers: dict[str, set[str]] = {}
    for entry in [*portfolio.get("positions", []), *candidates.get("candidates", [])]:
        tickers.setdefault(str(entry["ticker"]), set()).add(str(entry["asset_class"]).lower())
    return tickers


def _validate_artifacts(task: TaskSpec, record: ExecutionRecord) -> None:
    """Schema-validate recommendations and check report provenance."""
    text = record.artifacts.get("recommendations.json")
    if text is None:
        raise ValueError(f"execution {record.execution_id} produced no recommendations.json")
    validate_recommendations(text, _snapshot_tickers(task))
    report = record.artifacts.get("report.md")
    if report is None or not report.strip():
        raise ValueError(f"execution {record.execution_id} produced no non-empty report.md")
    digest = record.bundle_digest
    if digest and digest not in report:
        raise ValueError(
            f"report.md for {record.execution_id} does not record bundle digest {digest}"
        )


def fetch_task(
    base: Path,
    day: date,
    portfolio_source: PortfolioSnapshot,
    provider: PriceProvider,
    *,
    scoreboard_path: Path | None = None,
    sp500_fetch: Callable[[str], str] | None = None,
    crypto_get: Callable | None = None,
) -> TaskSpec:
    """Build a frozen snapshot task from a fetched portfolio and market data.

    Summaries cover holdings plus the SPY/QQQ/BTC/ETH benchmarks; the
    S&P 500 + top-100 crypto universe is screened to 30 candidates. Prior
    outcomes come from ``outcomes_for`` when a scoreboard file exists.
    """
    base = Path(base)
    wanted: dict[str, str] = {}
    for position in portfolio_source.positions:
        wanted.setdefault(position.ticker, position.asset_class)
    for ticker, asset_class in BENCHMARKS.items():
        wanted.setdefault(ticker, asset_class)
    summaries = {
        ticker: market_summary(
            ticker, asset_class, provider.closes(ticker, asset_class, day, HISTORY_DAYS)
        )
        for ticker, asset_class in wanted.items()
    }

    equity_tickers = sp500_tickers(sp500_fetch or _default_sp500_fetch)
    crypto_tickers = top_crypto_symbols(crypto_get or _default_crypto_get)[:100]
    universe: list[tuple[str, str]] = [(t, "equity") for t in equity_tickers]
    universe += [(t, "crypto") for t in crypto_tickers]
    universe_summaries = [
        market_summary(ticker, asset_class, provider.closes(ticker, asset_class, day, HISTORY_DAYS))
        for ticker, asset_class in universe
    ]
    candidates = screen_candidates(universe_summaries, limit=30)

    scoreboard = Path(scoreboard_path) if scoreboard_path is not None else base / "scoreboard.json"
    prior_outcomes = outcomes_for(scoreboard, day) if scoreboard.is_file() else []
    return build_snapshot_task(
        base, day, portfolio_source, summaries, candidates, len(universe), prior_outcomes
    )


def run_daily(store: RunStore, executor, task: TaskSpec, bundle_digest: str) -> ExecutionRecord:
    """Run exactly one execution of the adopted bundle and validate its output.

    Invalid artifacts raise ``ValueError`` and nothing is saved as delivered.
    """
    record = executor.execute(task, bundle_digest, f"{task.id}/daily")
    if record.task_id != task.id or record.execution_id != f"{task.id}/daily":
        raise ValueError("executor returned mismatched execution identity")
    if not record.delivered:
        raise ValueError(f"daily execution {record.execution_id} was not delivered")
    _validate_artifacts(task, record)
    store.save_execution(record)
    return record


def run_evolution(tasks, config: ExperimentConfig, store, executor, editor_factory, base_digest):
    """Delegate to ``run_stream(..., learn=True)``; sequential ``k=1`` only."""
    if config.mode != "sequential":
        raise ValueError("portfolio evolution requires sequential mode")
    if config.k != 1:
        raise ValueError("portfolio evolution requires k=1")
    return run_stream(tasks, config, store, executor, editor_factory, base_digest, learn=True)


def read_adopted(base_dir: Path) -> str | None:
    path = Path(base_dir) / ADOPTED_FILE
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    digest = data.get("bundle_digest") if isinstance(data, dict) else None
    return str(digest) if digest else None


def write_adopted(base_dir: Path, bundle_digest: str) -> None:
    base_dir = Path(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / ADOPTED_FILE).write_text(json.dumps({"bundle_digest": bundle_digest}, indent=2))


def ensure_adopted(store: RunStore, base_dir: Path, bundle_root: Path) -> str:
    """Return the adopted digest, publishing ``bundle_root`` on first use only."""
    existing = read_adopted(base_dir)
    if existing is not None:
        if not store.bundle_path(existing).is_dir():
            raise ValueError(f"adopted bundle {existing} is not in the run store")
        return existing
    digest = store.publish_bundle(Path(bundle_root))
    write_adopted(base_dir, digest)
    return digest


def evolved_digest(results, tasks, previous_digest: str) -> str:
    """Digest to adopt after weekly evolution (spec section 7.4).

    Adopts the final task's composed execution digest only when that
    execution was delivered and schema-valid; ``KEEP_BASE``/fallback,
    undelivered, and invalid outcomes keep the previous digest.
    """
    if not results:
        return previous_digest
    tasks_by_id = {t.id: t for t in tasks}
    last = max(
        results,
        key=lambda r: tasks_by_id.get(r.task_id).ordinal if tasks_by_id.get(r.task_id) else 0,
    )
    task = tasks_by_id.get(last.task_id)
    if task is None:
        return previous_digest
    chosen_id = last.selection.chosen_execution_id
    label = next((k for k, r in last.executions.items() if r.execution_id == chosen_id), None)
    chosen = last.executions.get(label) if label else None
    if chosen is None or not chosen.delivered:
        return previous_digest
    if label is None or label.startswith("base"):
        return previous_digest
    if chosen.bundle_digest == previous_digest:
        return previous_digest
    try:
        _validate_artifacts(task, chosen)
    except ValueError:
        return previous_digest
    return chosen.bundle_digest
