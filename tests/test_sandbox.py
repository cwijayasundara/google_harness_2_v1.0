import subprocess
from pathlib import Path

import pytest

from harness_clone.contracts import ExperimentConfig, TaskSpec


def test_artifacts_follow_contract_and_reject_symlinks(tmp_path):
    from harness_clone.sandbox import collect_artifacts
    task = TaskSpec(id="one",domain="code",prompt="make x",source_root=tmp_path,output_paths=["x.txt"])
    (tmp_path / "x.txt").write_text("")
    assert collect_artifacts(tmp_path, task) == ({},False,"missing-or-empty-output")
    assert collect_artifacts(tmp_path,task.model_copy(update={"allow_empty":True}))[1] is True
    (tmp_path / "x.txt").unlink()
    (tmp_path / "x.txt").symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="symlink"):
        collect_artifacts(tmp_path,task)


def test_docker_invocation_enforces_boundaries(tmp_path):
    from harness_clone.sandbox import worker_command
    cmd = worker_command("job-one", "net-one", "gateway", "scoped-token", tmp_path, ExperimentConfig())
    assert "--read-only" in cmd
    assert "--cap-drop=ALL" in cmd
    assert "--security-opt=no-new-privileges" in cmd
    assert "net-one" == cmd[cmd.index("--network")+1]
    assert not any("docker.sock" in arg or "FIREWORKS_API_KEY" in arg for arg in cmd)
    assert "65532:65532" in cmd


def test_timeout_and_exit_failure_are_recorded(tmp_path, monkeypatch):
    from harness_clone.sandbox import DockerExecutor
    from harness_clone.storage import RunStore
    store=RunStore(tmp_path / "runs")
    digest=store.publish_bundle(Path(__file__).parents[1]/"examples/base-harness")
    task=TaskSpec(id="one",domain="code",prompt="make x",source_root=tmp_path,output_paths=["x.txt"])
    executor=DockerExecutor(store,ExperimentConfig(max_usd="1"))
    def fail(*a,**kw):
        raise subprocess.TimeoutExpired("docker",1)
    monkeypatch.setattr(executor,"_launch",fail)
    result=executor.execute(task,digest,"base1")
    assert result.status == "failed" and result.delivered is False
    assert result.error == "timeout"


def test_task_source_and_outputs_cannot_escape(tmp_path):
    from harness_clone.tasks import load_task
    (tmp_path/"source").mkdir()
    f=tmp_path/"task.json"
    f.write_text('{"id":"a","domain":"code","prompt":"x","source_root":"source","output_paths":["../x"]}')
    with pytest.raises(ValueError):
        load_task(f)


def test_gateway_enforces_model_budget_and_unknown_usage(tmp_path):
    from harness_clone.gateway import InferenceGateway
    from harness_clone.storage import BudgetLedger
    ledger=BudgetLedger(tmp_path/"b.sqlite",__import__('decimal').Decimal("1"))
    gateway=InferenceGateway(ExperimentConfig(max_usd="1"), ledger, "token")
    with pytest.raises(ValueError,match="model"):
        gateway.prepare({"model":"other","messages":[]})
    with pytest.raises(ValueError,match="token"):
        gateway.prepare({"model":ExperimentConfig().model,"messages":[]},token="wrong")
    request_id,payload=gateway.prepare({"model":ExperimentConfig().model,"messages":[{"role":"user","content":"hi"}]},token="token")
    assert payload["max_tokens"] == 4096
    gateway.finish(request_id,{"choices":[]})
    with pytest.raises(ValueError,match="unknown"):
        gateway.prepare({"model":ExperimentConfig().model,"messages":[]},token="token")
