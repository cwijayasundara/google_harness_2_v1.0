import subprocess
from pathlib import Path

import pytest

from harness_clone.contracts import ExperimentConfig, TaskSpec


def test_artifacts_follow_contract_and_reject_symlinks(tmp_path):
    from harness_clone.sandbox import collect_artifacts

    task = TaskSpec(
        id="one", domain="code", prompt="make x", source_root=tmp_path, output_paths=["x.txt"]
    )
    (tmp_path / "x.txt").write_text("")
    assert collect_artifacts(tmp_path, task) == ({}, False, "missing-or-empty-output")
    assert collect_artifacts(tmp_path, task.model_copy(update={"allow_empty": True}))[1] is True
    (tmp_path / "x.txt").unlink()
    (tmp_path / "x.txt").symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="symlink"):
        collect_artifacts(tmp_path, task)


def test_docker_invocation_enforces_boundaries(tmp_path):
    from harness_clone.sandbox import worker_command

    cmd = worker_command(
        "job-one", "net-one", "gateway", "scoped-token", tmp_path, ExperimentConfig()
    )
    assert "--read-only" in cmd
    assert "--cap-drop=ALL" in cmd
    assert "--security-opt=no-new-privileges" in cmd
    assert "net-one" == cmd[cmd.index("--network") + 1]
    assert not any("docker.sock" in arg or "FIREWORKS_API_KEY" in arg for arg in cmd)
    assert "65532:65532" in cmd


def test_timeout_and_exit_failure_are_recorded(tmp_path, monkeypatch):
    from harness_clone.sandbox import DockerExecutor
    from harness_clone.storage import RunStore

    store = RunStore(tmp_path / "runs")
    digest = store.publish_bundle(Path(__file__).parents[1] / "examples/base-harness")
    task = TaskSpec(
        id="one", domain="code", prompt="make x", source_root=tmp_path, output_paths=["x.txt"]
    )
    executor = DockerExecutor(store, ExperimentConfig(max_usd="1"))

    def fail(*a, **kw):
        raise subprocess.TimeoutExpired("docker", 1)

    monkeypatch.setattr(executor, "_launch", fail)
    result = executor.execute(task, digest, "base1")
    assert result.status == "failed" and result.delivered is False
    assert result.error == "timeout"


def test_task_source_and_outputs_cannot_escape(tmp_path):
    from harness_clone.tasks import load_task

    (tmp_path / "source").mkdir()
    f = tmp_path / "task.json"
    f.write_text(
        '{"id":"a","domain":"code","prompt":"x","source_root":"source","output_paths":["../x"]}'
    )
    with pytest.raises(ValueError):
        load_task(f)


def test_gateway_enforces_model_budget_and_unknown_usage(tmp_path):
    from harness_clone.gateway import InferenceGateway
    from harness_clone.storage import BudgetLedger

    ledger = BudgetLedger(tmp_path / "b.sqlite", __import__("decimal").Decimal("1"))
    gateway = InferenceGateway(ExperimentConfig(max_usd="1"), ledger, "token")
    with pytest.raises(ValueError, match="model"):
        gateway.prepare({"model": "other", "messages": []})
    with pytest.raises(ValueError, match="token"):
        gateway.prepare({"model": ExperimentConfig().model, "messages": []}, token="wrong")
    request_id, payload = gateway.prepare(
        {"model": ExperimentConfig().model, "messages": [{"role": "user", "content": "hi"}]},
        token="token",
    )
    assert payload["max_tokens"] == 4096
    gateway.finish(request_id, {"choices": []})
    with pytest.raises(ValueError, match="unknown"):
        gateway.prepare({"model": ExperimentConfig().model, "messages": []}, token="token")


def test_gateway_rejects_unmetered_generation_overrides(tmp_path):
    from decimal import Decimal
    from harness_clone.gateway import InferenceGateway
    from harness_clone.storage import BudgetLedger

    gateway = InferenceGateway(
        ExperimentConfig(max_usd="1"), BudgetLedger(tmp_path / "b.sqlite", Decimal("1")), "token"
    )
    with pytest.raises(ValueError, match="unsupported"):
        gateway.prepare(
            {"model": ExperimentConfig().model, "messages": [], "max_completion_tokens": 1000000},
            token="token",
        )


@pytest.mark.docker
def test_portfolio_math_executes_in_container():
    import os

    if os.environ.get("HARNESS_TEST_DOCKER") != "1":
        pytest.skip("set HARNESS_TEST_DOCKER=1 after building the executor image")
    scripts = Path(__file__).resolve().parents[1] / "examples/portfolio-harness/scripts"
    code = (
        "import sys; sys.path.insert(0, '/portfolio-scripts')\n"
        "import portfolio_math\n"
        "w = portfolio_math.weights([{'ticker': 'AAPL', 'units': 2, 'current_price': 50.0},"
        " {'ticker': 'MSFT', 'units': 1, 'current_price': 100.0}])\n"
        "assert w == {'AAPL': 0.5, 'MSFT': 0.5}, w\n"
        "assert abs(portfolio_math.drift({'AAPL': 0.6}, {'AAPL': 0.5})['AAPL'] - 0.1) < 1e-12\n"
        "assert portfolio_math.volatility([100.0, 101.0, 102.0]) > 0\n"
    )
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--user=65532:65532",
            "--cap-drop=ALL",
            "--tmpfs",
            "/tmp:rw,mode=1777",
            "--mount",
            f"type=bind,src={scripts},dst=/portfolio-scripts,readonly",
            "harness-clone-executor:0.1",
            "python",
            "-c",
            code,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_artifact_collection_allows_host_ancestor_alias_but_no_candidate_symlink(tmp_path):
    from harness_clone.sandbox import collect_artifacts

    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    root = alias / "nested" / "output"
    root.mkdir(parents=True)
    (root / "x.txt").write_text("ok")
    task = TaskSpec(id="x", domain="code", prompt="x", source_root=tmp_path, output_paths=["x.txt"])
    assert collect_artifacts(root, task)[0] == {"x.txt": "ok"}
