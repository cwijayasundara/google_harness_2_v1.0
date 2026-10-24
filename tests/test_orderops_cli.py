import json
from pathlib import Path

from harness_clone.cli import main


def test_cli_generates_free_cases(tmp_path, capsys):
    assert (
        main(
            [
                "orderops",
                "generate",
                "--tasks-dir",
                str(tmp_path / "tasks"),
                "--private-dir",
                str(tmp_path / "private"),
                "--dev-count",
                "2",
                "--holdout-count",
                "1",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["development_cases"] == 2
    assert (tmp_path / "private/answers.json").is_file()


def test_orderops_live_run_requires_explicit_cap(tmp_path, capsys):
    assert (
        main(
            [
                "orderops",
                "run",
                "--tasks-dir",
                str(tmp_path / "tasks"),
                "--run-dir",
                str(tmp_path / "run"),
            ]
        )
        == 2
    )
    assert "spending cap" in capsys.readouterr().err
    assert not (tmp_path / "run").exists()


def test_fresh_portfolio_explains_retirement():
    import pytest
    from harness_clone.cli import portfolio_bundle

    with pytest.raises(ValueError, match="retired.*orderops"):
        portfolio_bundle()


def test_orderops_bundle_and_csv_inspector_work(tmp_path):
    import subprocess
    import sys

    from harness_clone.bundles import validate_bundle

    bundle = Path(__file__).parents[1] / "examples/orderops-harness"
    validate_bundle(bundle)
    source = tmp_path / "sample.csv"
    source.write_text("id,amount\nX,100\nY,200\n")
    reply = subprocess.run(
        [sys.executable, str(bundle / "scripts/inspect_csv.py"), str(source)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(reply.stdout)["rows"] == [
        {"row": 2, "values": {"id": "X", "amount": "100"}},
        {"row": 3, "values": {"id": "Y", "amount": "200"}},
    ]


def test_executor_rejects_invalid_artifacts_before_publishing(tmp_path, monkeypatch):
    import subprocess

    from harness_clone.contracts import ExperimentConfig, TaskSpec
    from harness_clone.orderops.contracts import validate_artifacts
    from harness_clone.sandbox import DockerExecutor
    from harness_clone.storage import RunStore

    source = tmp_path / "source"
    source.mkdir()
    (source / "orders.csv").write_text("order_id\nO-1\n")
    task = TaskSpec(
        id="invalid",
        domain="orderops",
        prompt="investigate",
        source_root=source,
        output_paths=["findings.json", "proposed_actions.json", "report.md"],
    )
    store = RunStore(tmp_path / "run")
    digest = store.publish_bundle(Path(__file__).parents[1] / "examples/base-harness")
    executor = DockerExecutor(store, ExperimentConfig(), artifact_validator=validate_artifacts)

    def launch(work):
        (work / "output/findings.json").write_text("[]")
        (work / "output/proposed_actions.json").write_text("[]")
        (work / "output/report.md").write_text("finished")
        (work / "scratch/terminal.json").write_text('{"trace": []}')
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(executor, "_launch", launch)
    record = executor.execute(task, digest, "invalid-output")
    assert not record.delivered
    assert record.error.startswith("artifact-contract:")
    assert not store.completed_execution("invalid-output").delivered


def test_orderops_bundle_compiles_with_real_middleware_contract(tmp_path, monkeypatch):
    import shutil

    from deepagents.backends import LocalShellBackend
    from fakes import ScriptedModel

    from harness_clone.compiler import compile_agent
    from harness_clone.contracts import ExperimentConfig

    monkeypatch.setattr("harness_clone.compiler.make_model", lambda *a, **k: ScriptedModel())
    bundle = tmp_path / "bundle"
    shutil.copytree(Path(__file__).parents[1] / "examples/orderops-harness", bundle)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    graph = compile_agent(bundle, LocalShellBackend(root_dir=runtime), ExperimentConfig())
    assert (
        graph.invoke({"messages": [{"role": "user", "content": "hello"}]})["messages"][-1].content
        == "done"
    )


def test_cli_runs_and_evaluates_without_exposing_private_answers(tmp_path, monkeypatch, capsys):
    from decimal import Decimal

    from test_orderops_experiment import Editor, Executor

    import harness_clone.cli as cli
    from harness_clone.contracts import ExperimentConfig

    tasks, private, run = tmp_path / "tasks", tmp_path / "private", tmp_path / "run"
    assert (
        main(
            [
                "orderops",
                "generate",
                "--tasks-dir",
                str(tasks),
                "--private-dir",
                str(private),
                "--dev-count",
                "2",
                "--holdout-count",
                "1",
            ]
        )
        == 0
    )
    capsys.readouterr()
    monkeypatch.setattr(cli, "_admit_live", lambda value: Decimal(value))
    monkeypatch.setattr(
        cli, "_pinned_config", lambda cap, **kw: ExperimentConfig(max_usd=cap, **kw)
    )
    monkeypatch.setattr(
        cli,
        "live_components",
        lambda config, store: (Executor(store), lambda task: Editor(task, [])),
    )
    assert (
        main(
            [
                "orderops",
                "run",
                "--tasks-dir",
                str(tasks),
                "--run-dir",
                str(run),
                "--max-usd",
                "2",
                "--arms",
                "frozen",
                "persistent",
            ]
        )
        == 0
    )
    online = json.loads(capsys.readouterr().out)
    assert set(online["arms"]) == {"frozen", "persistent"}
    output = tmp_path / "evaluation.json"
    assert (
        main(
            [
                "orderops",
                "evaluate",
                "--run-dir",
                str(run),
                "--answers",
                str(private / "answers.json"),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    offline = json.loads(capsys.readouterr().out)
    assert offline["arms"]["frozen"]["heldout"]["cases"] == 1
    assert len(offline["paired_heldout"]) == 1
