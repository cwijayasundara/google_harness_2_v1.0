"""Local command interface; live calls always require an explicit spending cap."""

import argparse
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from .contracts import PI_IMAGE, ExperimentConfig, TaskResult, TaskSpec
from .demo import default_bundle, run_demo, summarize
from .storage import BudgetLedger, RunStore, fingerprint


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
            if Decimal(args.max_usd) <= 0:
                raise ValueError("live run requires an explicit nonzero spending cap via --max-usd")
            if not os.environ.get("FIREWORKS_API_KEY"):
                raise ValueError("FIREWORKS_API_KEY is not configured")
            if not docker_available():
                raise ValueError("Docker daemon unavailable; start Docker before live execution")
            from .tasks import load_task
            from .experience import check_stream, run_stream

            defaults = ExperimentConfig()
            config = ExperimentConfig(
                mode=args.mode,
                k=args.k,
                profile=args.profile,
                editor_policy=args.editor_policy,
                max_usd=Decimal(args.max_usd),
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
