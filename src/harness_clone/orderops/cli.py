"""CLI adapter; answer keys are opened only by the offline evaluate command."""

from pathlib import Path

from ..storage import RunStore


def default_bundle():
    source = Path(__file__).resolve().parents[3] / "examples/orderops-harness"
    return source if source.is_dir() else Path(__file__).parents[1] / "examples/orderops-harness"


def add_parser(commands):
    parser = commands.add_parser("orderops", help="Synthetic operations harness benchmark")
    sub = parser.add_subparsers(dest="orderops_command", required=True)
    generate = sub.add_parser("generate", help="Generate public tasks and separate private answers")
    generate.add_argument("--tasks-dir", type=Path, required=True)
    generate.add_argument("--private-dir", type=Path, required=True)
    generate.add_argument("--seed", type=int, default=42)
    generate.add_argument("--dev-count", type=int, default=30)
    generate.add_argument("--holdout-count", type=int, default=20)
    run = sub.add_parser("run", help="Run live evolution and frozen held-out executions")
    run.add_argument("--tasks-dir", type=Path, required=True)
    run.add_argument("--run-dir", type=Path, required=True)
    run.add_argument("--base-harness", type=Path, default=default_bundle())
    run.add_argument("--max-usd", default="0")
    run.add_argument("--k", type=int, default=1)
    run.add_argument("--editor-policy", choices=["pi", "text"], default="pi")
    run.add_argument(
        "--arms",
        nargs="+",
        choices=["frozen", "local", "persistent", "retries"],
        default=["frozen", "local", "persistent", "retries"],
    )
    evaluate = sub.add_parser("evaluate", help="Score outputs offline without feeding back answers")
    evaluate.add_argument("--run-dir", type=Path, required=True)
    evaluate.add_argument("--answers", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)


def dispatch(args):
    if args.orderops_command == "generate":
        from .cases import generate_suite

        return generate_suite(
            args.tasks_dir,
            args.private_dir,
            seed=args.seed,
            dev_count=args.dev_count,
            holdout_count=args.holdout_count,
        )
    if args.orderops_command == "evaluate":
        from .score import evaluate_experiment

        return evaluate_experiment(args.run_dir, args.answers, args.output)
    from ..cli import _admit_live, _pinned_config, live_components
    from .contracts import validate_artifacts
    from .experiment import load_suite, run_experiment

    cap = _admit_live(args.max_usd)
    tasks = load_suite(args.tasks_dir)
    config = _pinned_config(
        cap, mode="sequential", profile="practical", k=args.k, editor_policy=args.editor_policy
    )
    store = RunStore(args.run_dir)
    # Publication is content-addressed; incompatible resume admission remains untouched.
    base = store.publish_bundle(args.base_harness)
    executor, factory = live_components(config, store)
    executor.artifact_validator = validate_artifacts
    summary = run_experiment(tasks, config, store, executor, factory, base, arms=args.arms)
    return {
        "run_dir": str(store.root),
        "arms": {
            name: {
                "development_cases": len(arm["development"]),
                "heldout_cases": len(arm["heldout"]),
                "final_digest": arm["final_digest"],
            }
            for name, arm in summary["arms"].items()
        },
        "budget": summary["budget"],
        "note": "Execution complete. Run orderops evaluate separately to measure quality.",
    }
