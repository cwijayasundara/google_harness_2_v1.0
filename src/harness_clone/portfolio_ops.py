"""Portfolio operations: snapshot fetching, daily execution, weekly evolution.

Executor and store are injected so tests run with fakes and no Docker,
network, or model. Host-side market/portfolio fetching never enters a
candidate bundle.
"""

import json
import shutil
import tempfile
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path

from .bundles import bundle_digest
from .contracts import ExecutionRecord, ExperimentConfig, PortfolioSnapshot, TaskSpec
from .experience import run_stream
from .portfolio_artifacts import validate_recommendations
from .portfolio_market import (
    COIN_IDS,
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
MISSING_SAMPLE = 10
ADOPTED_FILE = "adopted.json"
ARCHIVE_DIR = "bundles"


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

    def _route(self, asset_class: str):
        return self._crypto if asset_class == "crypto" else self._equity

    def closes(self, ticker: str, asset_class: str, end: date, days: int) -> list[float]:
        return self._route(asset_class).closes(ticker, asset_class, end, days)

    def dated_closes(self, ticker: str, asset_class: str, end: date, days: int):
        return self._route(asset_class).dated_closes(ticker, asset_class, end, days)

    def add_coin_ids(self, coin_ids: dict[str, str]) -> None:
        if hasattr(self._crypto, "add_coin_ids"):
            self._crypto.add_coin_ids(coin_ids)

    def has_coin_id(self, ticker: str) -> bool:
        has = getattr(self._crypto, "has_coin_id", None)
        return True if has is None else bool(has(ticker))


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
    """Schema-validate recommendations and require a non-empty report.

    The agent is never told its bundle digest, so provenance is not
    required inside ``report.md``; :func:`stamp_report` adds it host-side.
    """
    text = record.artifacts.get("recommendations.json")
    if text is None:
        raise ValueError(f"execution {record.execution_id} produced no recommendations.json")
    validate_recommendations(text, _snapshot_tickers(task))
    report = record.artifacts.get("report.md")
    if report is None or not report.strip():
        raise ValueError(f"execution {record.execution_id} produced no non-empty report.md")


def _snapshot_fetched_at(task: TaskSpec) -> str:
    try:
        data = json.loads((Path(task.source_root) / "portfolio.json").read_text())
        return str(data["fetched_at"])
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError(f"cannot read snapshot fetch time for {task.id}: {exc}") from exc


def _age(fetched_at: str, now: datetime) -> str:
    try:
        fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return "unknown (unparseable fetch timestamp)"
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    minutes = int((now - fetched).total_seconds() // 60)
    if minutes < 0:
        return "unknown (fetch timestamp is in the future)"
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    return (f"{days}d " if days else "") + f"{hours}h {mins}m"


def stamp_report(task: TaskSpec, record: ExecutionRecord, now: datetime) -> str:
    """Prefix the agent's report with host-verified provenance (spec 5.2)."""
    fetched_at = _snapshot_fetched_at(task)
    header = "\n".join(
        [
            "<!-- provenance stamped by the host, not written by the agent -->",
            f"Bundle digest: {record.bundle_digest}",
            f"Execution: {record.execution_id}",
            f"Snapshot fetched at: {fetched_at}",
            f"Snapshot age at delivery: {_age(fetched_at, now)} "
            f"(as of {now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})",
            "Research only; not financial advice. No orders are placed.",
            "",
            "---",
            "",
        ]
    )
    return header + record.artifacts["report.md"].lstrip()


def write_delivered_report(task: TaskSpec, record: ExecutionRecord, now: datetime) -> Path:
    """Write the stamped report and recommendations beside the dated snapshot.

    Files land in ``<base>/<date>/`` (the parent of the frozen ``source/``),
    so the snapshot evidence and stream signature are untouched.
    """
    dated_dir = Path(task.source_root).parent
    report = dated_dir / "report.md"
    report.write_text(stamp_report(task, record, now))
    (dated_dir / "recommendations.json").write_text(record.artifacts["recommendations.json"])
    return report


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

    sources_unavailable: list[str] = []
    try:
        equity_tickers = sp500_tickers(sp500_fetch or _default_sp500_fetch)
    except Exception:  # noqa: BLE001 - a missing list is reported, never fatal
        equity_tickers, sources_unavailable = [], [*sources_unavailable, "sp500"]
    try:
        crypto_pairs = top_crypto_symbols(crypto_get or _default_crypto_get)[:100]
    except Exception:  # noqa: BLE001 - a missing list is reported, never fatal
        crypto_pairs, sources_unavailable = [], [*sources_unavailable, "crypto_top100"]
    if crypto_pairs and hasattr(provider, "add_coin_ids"):
        provider.add_coin_ids(dict(crypto_pairs))
    # Persisted for the scorer; the curated map wins, as it does on the provider.
    coin_ids = {symbol.upper(): coin_id for symbol, coin_id in crypto_pairs}
    for ticker, asset_class in wanted.items():
        if asset_class == "crypto" and ticker.upper() in COIN_IDS:
            coin_ids.setdefault(ticker.upper(), COIN_IDS[ticker.upper()])
    coin_ids = {s: COIN_IDS.get(s, cid) for s, cid in coin_ids.items()}
    universe: list[tuple[str, str]] = [(t, "equity") for t in equity_tickers]
    universe += [(symbol, "crypto") for symbol, _ in crypto_pairs]
    universe_summaries = []
    missing: list[str] = []
    for ticker, asset_class in universe:
        # Holdings and benchmarks above fail loudly; one universe ticker
        # failing (404, 429, empty series) is recorded as missing instead.
        try:
            closes = provider.closes(ticker, asset_class, day, HISTORY_DAYS)
        except Exception:  # noqa: BLE001
            closes = []
        summary = market_summary(ticker, asset_class, closes)
        if summary.missing:
            missing.append(ticker)
        universe_summaries.append(summary)
    candidates = screen_candidates(universe_summaries, limit=30)
    universe_missing = {
        "count": len(missing),
        "sample": sorted(missing)[:MISSING_SAMPLE],
        "sources_unavailable": sources_unavailable,
    }

    scoreboard = Path(scoreboard_path) if scoreboard_path is not None else base / "scoreboard.json"
    prior_outcomes = outcomes_for(scoreboard, day) if scoreboard.is_file() else []
    return build_snapshot_task(
        base,
        day,
        portfolio_source,
        summaries,
        candidates,
        len(universe),
        prior_outcomes,
        universe_missing=universe_missing,
        coin_ids=coin_ids,
    )


def coverage_warnings(task: TaskSpec) -> tuple[dict | None, list[str]]:
    """Read a snapshot's universe coverage and describe any gap as warnings."""
    path = Path(task.source_root) / "candidates.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None, [f"cannot read universe coverage from {path}"]
    missing = data.get("universe_missing")
    if not isinstance(missing, dict):
        return None, []
    warnings = []
    if missing.get("count"):
        warnings.append(
            f"universe coverage partial: {missing['count']} of "
            f"{data.get('universe_considered')} tickers had no data "
            f"(e.g. {', '.join(missing.get('sample', []))})"
        )
    for source in missing.get("sources_unavailable", []):
        warnings.append(f"universe source unavailable: {source}")
    return missing, warnings


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


def archived_bundle(base_dir: Path, digest: str) -> Path:
    """Where the adopted bundle ``digest`` is kept outside any one run store."""
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("invalid bundle digest")
    return Path(base_dir) / ARCHIVE_DIR / digest


def archive_bundle(base_dir: Path, store: RunStore, digest: str) -> Path:
    """Copy ``digest`` from ``store`` into ``<base>/bundles/`` (digest-verified).

    The archive lets every later daily/evolve run directory import the
    adopted bundle, so no run store has to live forever.
    """
    source = store.bundle_path(digest)
    if not source.is_dir():
        raise ValueError(f"bundle {digest} is not in the run store {store.root}")
    dest = archived_bundle(base_dir, digest)
    if dest.is_dir():
        if bundle_digest(dest) != digest:
            raise ValueError(f"archived bundle {digest} does not match its digest")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        stage = Path(tmp) / "bundle"
        shutil.copytree(source, stage)
        if bundle_digest(stage) != digest:
            raise ValueError(f"bundle {digest} changed while archiving")
        stage.rename(dest)
    return dest


def write_adopted(base_dir: Path, digest: str, store: RunStore) -> None:
    """Archive ``digest`` from ``store`` and record it as adopted."""
    base_dir = Path(base_dir)
    archive_bundle(base_dir, store, digest)
    (base_dir / ADOPTED_FILE).write_text(json.dumps({"bundle_digest": digest}, indent=2))


def import_bundle(store: RunStore, base_dir: Path, digest: str) -> None:
    """Make archived bundle ``digest`` available in ``store`` (no-op if present)."""
    if store.bundle_path(digest).is_dir():
        return
    archive = archived_bundle(base_dir, digest)
    if not archive.is_dir():
        raise ValueError(f"adopted bundle {digest} is not archived under {archive.parent}")
    if bundle_digest(archive) != digest or store.publish_bundle(archive) != digest:
        raise ValueError(f"archived bundle {digest} does not match its digest")


def ensure_adopted(store: RunStore, base_dir: Path, bundle_root: Path) -> str:
    """Return the adopted digest, available in ``store``.

    On first use ``bundle_root`` is published, archived, and adopted. Later
    calls import the archived adopted bundle into ``store``, so each daily
    or weekly run directory can be fresh.
    """
    existing = read_adopted(base_dir)
    if existing is not None:
        import_bundle(store, base_dir, existing)
        return existing
    digest = store.publish_bundle(Path(bundle_root))
    write_adopted(base_dir, digest, store)
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
