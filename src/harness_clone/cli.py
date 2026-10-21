"""Local command interface; live calls always require an explicit spending cap."""

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from .contracts import PI_IMAGE, ExperimentConfig, SelectionRecord, TaskResult, TaskSpec
from .demo import default_bundle, run_demo, summarize
from .portfolio_etoro import EtoroClient, load_portfolio_csv
from .portfolio_ops import (
    coverage_warnings,
    ensure_adopted,
    evolved_digest,
    fetch_task,
    live_price_provider,
    run_daily,
    run_evolution,
    write_adopted,
    write_delivered_report,
)
from .portfolio_score import PRICE_LOOKBACK_DAYS, window_prices
from .portfolio_score import scoreboard as portfolio_scoreboard
from .storage import BudgetLedger, RunStore, fingerprint
from .tasks import load_task


def resolve_image_id(tag: str) -> str:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"cannot resolve runtime image {tag}") from exc
    image_id = result.stdout.strip()
    if result.returncode != 0 or not image_id:
        raise ValueError(f"cannot resolve runtime image {tag}")
    return image_id


def verify_runtime_images(config: ExperimentConfig, resolver=None) -> None:
    resolver = resolver or resolve_image_id
    for tag, expected in [(config.image, config.image_id), (PI_IMAGE, config.pi_image_id)]:
        if expected and resolver(tag) != expected:
            raise ValueError("runtime image changed since run admission; use a new run directory")


def docker_available():
    try:
        return (
            subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=5
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def resume_experiment(experiment_id: str, store: RunStore):
    from .experience import run_stream

    config = ExperimentConfig.model_validate(store.get("experiment", "config"))
    if fingerprint(config.model_dump(mode="json")) != experiment_id:
        raise ValueError("experiment identity mismatch")
    verify_runtime_images(config)
    runtime = store.get("runtime", "kind") or {}
    tasks = [TaskSpec.model_validate(t) for t in store.get("runtime", "tasks") or []]
    signature = store.get("stream", "signature")
    if not tasks or not signature:
        raise ValueError("run has no persisted task stream")
    if runtime.get("backend") == "deterministic-demo":
        from .demo import DemoEditor, DemoExecutor

        executor = DemoExecutor()

        def factory(task):
            return DemoEditor()
    else:
        if not docker_available():
            raise ValueError("Docker daemon unavailable")
        executor, factory = live_components(config, store)
    results = run_stream(tasks, config, store, executor, factory, signature["base"])
    store.put("runtime", "results", [r.model_dump(mode="json") for r in results])
    return summarize(results, runtime.get("backend", "live"))


def live_components(config, store):
    from .editors import DeepAgentEditor
    from .pi_editor import PiEditor
    from .sandbox import DockerExecutor

    editor = PiEditor if config.editor_policy == "pi" else DeepAgentEditor
    return DockerExecutor(store, config), lambda task: editor(task, config, store)


def portfolio_bundle() -> Path:
    source = Path(__file__).resolve().parents[2] / "examples/portfolio-harness"
    if source.exists():
        return source
    return Path(__file__).parent / "examples/portfolio-harness"


def _admit_live(max_usd: str) -> Decimal:
    """The ``run`` branch's live guards: nonzero cap, key present, Docker up."""
    cap = Decimal(max_usd)
    if cap <= 0:
        raise ValueError("live run requires an explicit nonzero spending cap via --max-usd")
    if not os.environ.get("FIREWORKS_API_KEY"):
        raise ValueError("FIREWORKS_API_KEY is not configured")
    if not docker_available():
        raise ValueError("Docker daemon unavailable; start Docker before live execution")
    return cap


def _pinned_config(cap: Decimal, **overrides) -> ExperimentConfig:
    defaults = ExperimentConfig()
    return ExperimentConfig(
        max_usd=cap,
        image_id=resolve_image_id(defaults.image),
        pi_image_id=resolve_image_id(PI_IMAGE),
        **overrides,
    )


def _parse_day(value) -> date:
    if value is None:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid --date {value!r}: expected YYYY-MM-DD") from exc


def _etoro_get(url: str, headers: dict) -> dict:
    import httpx

    response = httpx.get(url, headers=headers, timeout=30.0)
    response.raise_for_status()
    return response.json()


def _portfolio_source(args):
    if args.csv is not None:
        fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return load_portfolio_csv(args.csv, fetched_at)
    return EtoroClient.from_env(_etoro_get).fetch_portfolio()


def _fetch_or_load_task(args, base_dir: Path, day: date) -> TaskSpec:
    dated = base_dir / day.isoformat() / "task.json"
    if dated.is_file():
        return load_task(dated)
    return fetch_task(
        base_dir,
        day,
        _portfolio_source(args),
        live_price_provider(),
        scoreboard_path=args.scoreboard,
    )


def _week_tasks(base_dir: Path, day: date) -> list[TaskSpec]:
    tasks = []
    if base_dir.is_dir():
        for child in sorted(base_dir.iterdir()):
            try:
                child_day = date.fromisoformat(child.name)
            except ValueError:
                continue
            if child_day <= day and (child / "task.json").is_file():
                tasks.append(load_task(child / "task.json"))
    tasks.sort(key=lambda t: t.ordinal)
    if not tasks:
        raise ValueError(f"no portfolio snapshots found under {base_dir}")
    return tasks[-5:]


def _record_daily_result(store: RunStore, task: TaskSpec, record) -> None:
    tasks = [t for t in (store.get("runtime", "tasks") or []) if t.get("id") != task.id]
    tasks.append(task.model_dump(mode="json"))
    store.put("runtime", "tasks", tasks)
    result = TaskResult(
        task_id=task.id,
        executions={"daily": record},
        selection=SelectionRecord(chosen_execution_id=record.execution_id),
    )
    results = [r for r in (store.get("runtime", "results") or []) if r.get("task_id") != task.id]
    results.append(result.model_dump(mode="json"))
    store.put("runtime", "results", results)


def _portfolio_score(args, run_dir: Path, day: date) -> dict:
    out = args.scoreboard
    if out.resolve().is_relative_to(run_dir.resolve()):
        raise ValueError("scoreboard output must be outside the online run directory")
    from .contracts import TaskResult as _TaskResult
    from .portfolio_market import market_summary

    store = RunStore(run_dir)
    results = [_TaskResult.model_validate(r) for r in store.get("runtime", "results") or []]
    benchmarks = {"SPY": "etf", "QQQ": "etf", "BTC": "crypto", "ETH": "crypto"}
    tickers: set[str] = set()
    ticker_classes: dict[str, str] = {}
    for result in results:
        chosen = next(
            (
                r
                for r in result.executions.values()
                if r.execution_id == result.selection.chosen_execution_id
            ),
            None,
        )
        if chosen is None:
            continue
        try:
            for rec in json.loads(chosen.artifacts.get("recommendations.json", "[]")):
                tickers.add(str(rec["ticker"]))
                tickers.add(str(rec["benchmark"]))
                if rec.get("asset_class"):
                    ticker_classes[str(rec["ticker"])] = str(rec["asset_class"])
                ticker_classes.setdefault(
                    str(rec["benchmark"]), benchmarks.get(str(rec["benchmark"]), "equity")
                )
        except (ValueError, KeyError, TypeError):
            continue
    provider = live_price_provider()
    volatilities = {
        ticker: market_summary(
            ticker,
            ticker_classes.get(ticker, benchmarks.get(ticker, "equity")),
            provider.closes(
                ticker, ticker_classes.get(ticker, benchmarks.get(ticker, "equity")), day, 365
            ),
        ).volatility_30d
        for ticker in sorted(tickers)
    }

    def prices(ticker: str, asset_class: str, start: date, end: date):
        days = max((end - start).days, 0) + PRICE_LOOKBACK_DAYS
        return window_prices(provider.dated_closes(ticker, asset_class, end, days), start, end)

    return portfolio_scoreboard(run_dir, out, day, prices, volatilities)


def run_portfolio(args) -> dict:
    base_dir: Path = args.base_dir
    day = _parse_day(args.date)
    if args.portfolio_command == "fetch":
        task = fetch_task(
            base_dir,
            day,
            _portfolio_source(args),
            live_price_provider(),
            scoreboard_path=args.scoreboard,
        )
        report = {"task": task.id, "date": day.isoformat(), "source_root": str(task.source_root)}
        universe_missing, warnings = coverage_warnings(task)
        if universe_missing is not None:
            report["universe_missing"] = universe_missing
        if warnings:
            report["warnings"] = warnings
            for warning in warnings:
                print(f"harness-clone: warning: {warning}", file=sys.stderr)
        return report
    run_dir: Path = args.run_dir or (base_dir / "run")
    if args.portfolio_command == "score":
        return _portfolio_score(args, run_dir, day)
    cap = _admit_live(args.max_usd)
    store = RunStore(run_dir)
    digest = ensure_adopted(store, base_dir, portfolio_bundle())
    if args.portfolio_command == "daily":
        config = _pinned_config(cap)
        task = _fetch_or_load_task(args, base_dir, day)
        executor, _factory = live_components(config, store)
        record = run_daily(store, executor, task, digest)
        _record_daily_result(store, task, record)
        report_path = write_delivered_report(task, record, datetime.now(timezone.utc))
        return {
            "task": task.id,
            "execution": record.execution_id,
            "delivered": record.delivered,
            "bundle_digest": digest,
            "report": str(report_path),
        }
    config = _pinned_config(cap, mode="sequential", k=1)
    tasks = _week_tasks(base_dir, day)
    store.put("runtime", "kind", {"backend": "deepagents-fireworks"})
    store.put("runtime", "tasks", [t.model_dump(mode="json") for t in tasks])
    executor, factory = live_components(config, store)
    results = run_evolution(tasks, config, store, executor, factory, digest)
    store.put("runtime", "results", [r.model_dump(mode="json") for r in results])
    adopted = evolved_digest(results, tasks, digest)
    if adopted != digest:
        write_adopted(base_dir, adopted)
    report = summarize(results, "deepagents-fireworks")
    report.update({"previous_bundle_digest": digest, "adopted_bundle_digest": adopted})
    return report


def parser():
    p = argparse.ArgumentParser(prog="harness-clone")
    commands = p.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--live", action="store_true")
    doctor.add_argument("--max-usd", default="0")
    for name in ["demo", "run", "resume", "inspect", "evaluate"]:
        cmd = commands.add_parser(name)
        cmd.add_argument("--run-dir", type=Path, required=True)
        if name in {"demo", "run"}:
            cmd.add_argument("--mode", choices=["parallel", "sequential"], default="parallel")
            cmd.add_argument("--k", type=int, default=1)
        if name == "run":
            cmd.add_argument("--task", type=Path, action="append", required=True)
            cmd.add_argument("--base-harness", type=Path, default=default_bundle())
            cmd.add_argument("--profile", choices=["research", "practical"], default="research")
            cmd.add_argument("--editor-policy", choices=["text", "pi"], default="pi")
            cmd.add_argument("--max-usd", default="0")
        if name == "evaluate":
            cmd.add_argument("--expectations", type=Path, required=True)
            cmd.add_argument("--output", type=Path, required=True)
    portfolio = commands.add_parser("portfolio")
    portfolio_commands = portfolio.add_subparsers(dest="portfolio_command", required=True)
    for name in ["fetch", "daily", "evolve", "score"]:
        cmd = portfolio_commands.add_parser(name)
        cmd.add_argument("--base-dir", type=Path, default=Path("runs/portfolio"))
        cmd.add_argument("--date", default=None)
        cmd.add_argument("--run-dir", type=Path, default=None)
        if name in {"fetch", "daily"}:
            cmd.add_argument("--csv", type=Path, default=None)
            cmd.add_argument("--scoreboard", type=Path, default=None)
        if name in {"daily", "evolve"}:
            cmd.add_argument("--max-usd", default="0")
        if name == "score":
            cmd.add_argument("--scoreboard", type=Path, required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            from .preflight import run_preflight

            report = run_preflight(ExperimentConfig(max_usd=Decimal(args.max_usd)), args.live)
            report.update(
                {
                    "docker_available": docker_available(),
                    "fireworks_key_configured": bool(os.environ.get("FIREWORKS_API_KEY")),
                }
            )
        elif args.command == "demo":
            report = run_demo(args.run_dir, args.mode, args.k)
        elif args.command == "run":
            cap = _admit_live(args.max_usd)
            from .tasks import load_task
            from .experience import check_stream, run_stream

            defaults = ExperimentConfig()
            config = ExperimentConfig(
                mode=args.mode,
                k=args.k,
                profile=args.profile,
                editor_policy=args.editor_policy,
                max_usd=cap,
                image_id=resolve_image_id(defaults.image),
                pi_image_id=resolve_image_id(PI_IMAGE),
            )
            tasks = [load_task(path) for path in args.task]
            store = RunStore(args.run_dir)
            store.create_experiment(config)
            base = store.publish_bundle(args.base_harness)
            # Admit stream identity before overwriting resumable runtime metadata,
            # so a rejected rerun leaves the original run untouched.
            check_stream(store, tasks, base)
            store.put("runtime", "kind", {"backend": "deepagents-fireworks"})
            store.put("runtime", "tasks", [t.model_dump(mode="json") for t in tasks])
            executor, factory = live_components(config, store)
            results = run_stream(tasks, config, store, executor, factory, base)
            store.put("runtime", "results", [r.model_dump(mode="json") for r in results])
            report = summarize(results, "deepagents-fireworks")
        elif args.command == "portfolio":
            report = run_portfolio(args)
        else:
            if not (args.run_dir / "run.sqlite").is_file():
                raise ValueError("run directory does not contain an experiment")
            store = RunStore(args.run_dir)
            if args.command == "resume":
                report = resume_experiment(fingerprint(store.get("experiment", "config")), store)
            elif args.command == "inspect":
                results = [
                    TaskResult.model_validate(r) for r in store.get("runtime", "results") or []
                ]
                report = summarize(
                    results, (store.get("runtime", "kind") or {}).get("backend", "unknown")
                )
                config = ExperimentConfig.model_validate(store.get("experiment", "config"))
                report["budget"] = BudgetLedger(
                    store.root / "budget.sqlite", config.max_usd
                ).summary()
            else:
                from .evaluate import evaluate

                report = evaluate(store, json.loads(args.expectations.read_text()), args.output)
        print(json.dumps(report, indent=2, default=str))
        return 1 if report.get("errors") or report.get("status") == "failed" else 0
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(f"harness-clone: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
