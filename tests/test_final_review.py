"""Regressions for the seven final-review defects (handoff 2026-10-21)."""

import json
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
from test_controller import FixtureEditor, FixtureExecutor

from harness_clone.contracts import (
    EditProposal,
    ExperimentConfig,
    HarnessPatch,
    TaskSpec,
    Usage,
)

BASE = Path(__file__).parents[1] / "examples/base-harness"


def make_task(tmp_path, name="one", values=(1, 2)):
    source = tmp_path / f"source-{name}"
    source.mkdir(exist_ok=True)
    (source / "values.json").write_text(json.dumps({"values": list(values)}))
    return TaskSpec(
        id=name,
        domain="code",
        prompt="Produce answer",
        source_root=source,
        output_paths=["answer.txt"],
    )


# --- Fix 1: rejected rerun must not poison resume metadata -----------------


class KeepBaseEditor:
    def propose(self, scope, parent_digest, evidence):
        return EditProposal(
            patch=HarnessPatch(parent_digest=parent_digest), rationale="keep", keep_base=True
        )

    def choose(self, task, records):
        return records[0].execution_id, "first"


def test_rejected_rerun_leaves_original_run_resumable(tmp_path, monkeypatch, capsys):
    import harness_clone.cli as cli

    monkeypatch.setenv("FIREWORKS_API_KEY", "test")
    monkeypatch.setattr(cli, "docker_available", lambda: True)
    monkeypatch.setattr(
        cli,
        "live_components",
        lambda config, store: (FixtureExecutor(), lambda t: KeepBaseEditor()),
    )
    monkeypatch.setattr(cli, "resolve_image_id", lambda tag: "sha256:test")

    def write_task(task, name):
        path = tmp_path / f"{name}.json"
        path.write_text(task.model_dump_json())
        return path

    task_a = make_task(tmp_path, "a", (1, 2))
    task_b = make_task(tmp_path, "b", (9, 9))
    run_dir = tmp_path / "run"
    args = ["run", "--run-dir", str(run_dir), "--max-usd", "1", "--editor-policy", "text"]
    assert cli.main([*args, "--task", str(write_task(task_a, "ta"))]) == 0
    capsys.readouterr()
    # Changed task inputs are correctly rejected...
    assert cli.main([*args, "--task", str(write_task(task_b, "tb"))]) == 2
    capsys.readouterr()
    # ...but the original run must still be resumable with its original tasks.
    from harness_clone.storage import RunStore

    persisted = RunStore(run_dir).get("runtime", "tasks")
    assert [t["id"] for t in persisted] == ["a"]
    assert cli.main(["resume", "--run-dir", str(run_dir)]) == 0


# --- Fix 2: stage ceilings and future-phase allocations --------------------


def gateway(tmp_path, stage_id="stage", stage_usd="0.01", cap="1"):
    from harness_clone.gateway import InferenceGateway
    from harness_clone.storage import BudgetLedger

    config = ExperimentConfig(max_usd=cap, stage_usd=stage_usd)
    return InferenceGateway(
        config, BudgetLedger(tmp_path / "b.sqlite", Decimal(cap)), "tok", stage_id
    )


def payload():
    return {"model": ExperimentConfig().model, "messages": [{"role": "user", "content": "hi"}]}


def test_gateway_rejects_request_above_stage_ceiling(tmp_path):
    gw = gateway(tmp_path, stage_usd="0.001")
    with pytest.raises(ValueError, match="stage"):
        gw.prepare(payload(), token="tok")


def test_gateway_stage_ceiling_is_cumulative_across_pending_requests(tmp_path):
    gw = gateway(tmp_path, stage_usd="0.01")
    gw.prepare(payload(), token="tok")
    with pytest.raises(ValueError, match="stage"):
        gw.prepare(payload(), token="tok")


def test_gateway_stage_ceiling_shared_across_repair_attempts(tmp_path):
    first = gateway(tmp_path, stage_id="task/x/inv1/attempt-0", stage_usd="0.01")
    second = gateway(tmp_path, stage_id="task/x/inv1/attempt-1", stage_usd="0.01")
    first.prepare(payload(), token="tok")
    with pytest.raises(ValueError, match="stage"):
        second.prepare(payload(), token="tok")


def test_phase_allocations_reserve_future_budget_without_double_counting(tmp_path):
    from harness_clone.storage import BudgetLedger

    ledger = BudgetLedger(tmp_path / "b.sqlite", Decimal("1"))
    ledger.allocate("phase-a", Decimal("0.6"))
    with pytest.raises(ValueError, match="cap"):
        ledger.allocate("phase-b", Decimal("0.6"))
    # Requests drawn on an allocation do not consume the cap a second time.
    ledger.reserve("phase-a/req-1", Decimal("0.5"), allocation_id="phase-a")
    ledger.allocate("phase-b", Decimal("0.4"))


def test_run_task_reserves_budget_for_later_phases_upfront(tmp_path):
    from harness_clone.controller import run_task
    from harness_clone.storage import RunStore

    store = RunStore(tmp_path / "runs")
    digest = store.publish_bundle(BASE)
    task = make_task(tmp_path)
    executor = FixtureExecutor()
    config = ExperimentConfig(max_usd=Decimal("1"), stage_usd=Decimal("0.3"), k=1)
    with pytest.raises(ValueError, match="cap"):
        run_task(task, config, store, executor, FixtureEditor(), digest)
    assert executor.calls == []


# --- Fix 3: Pi resume reconciles existing Docker resources -----------------


def fake_docker(existing_network=False, container_state=None):
    calls = []

    def run(args):
        calls.append(list(args))
        if "network" in args and "inspect" in args:
            return subprocess.CompletedProcess(args, 0 if existing_network else 1, "", "")
        if "inspect" in args:
            if container_state is None:
                return subprocess.CompletedProcess(args, 1, "", "no such object")
            return subprocess.CompletedProcess(args, 0, container_state + "\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    return run, calls


def test_existing_pi_network_is_reused_not_recreated():
    from harness_clone.pi_editor import ensure_network

    run, calls = fake_docker(existing_network=True)
    ensure_network(run, "hc-pi-net-x")
    assert not any("create" in c for c in calls)


def test_running_pi_container_is_reused_and_stopped_one_recreated():
    from harness_clone.pi_editor import ensure_container

    run, calls = fake_docker(container_state="true")
    assert ensure_container(run, "hc-pi-x", ["run", "-d", "--name", "hc-pi-x"]) is True
    assert not any(c[:1] == ["run"] for c in calls)

    run, calls = fake_docker(container_state="false")
    assert ensure_container(run, "hc-pi-x", ["run", "-d", "--name", "hc-pi-x"]) is False
    assert any(c[:2] == ["rm", "-f"] for c in calls)
    assert any(c[:1] == ["run"] for c in calls)


def test_pi_docker_failure_is_runtime_error_for_bounded_repair():
    from harness_clone.pi_editor import ensure_container

    def run(args):
        if "inspect" in args:
            return subprocess.CompletedProcess(args, 1, "", "")
        raise subprocess.CalledProcessError(1, args, stderr="daemon exploded")

    with pytest.raises(RuntimeError, match="docker"):
        ensure_container(run, "hc-pi-x", ["run", "-d"])


# --- Fix 4: abandoned provider calls become unknown ------------------------


def test_dead_gateway_requests_become_unknown_and_block_new_work(tmp_path):
    from harness_clone.storage import BudgetLedger

    ledger = BudgetLedger(tmp_path / "b.sqlite", Decimal("1"))
    ledger.reserve("stage-a/req-1", Decimal("0.1"))
    ledger.reserve("stage-b/req-1", Decimal("0.1"))
    ledger.mark_unknown("stage-a")
    assert ledger.usage_for("stage-a").known is False
    # The live stage is untouched and can still settle.
    ledger.settle("stage-b/req-1", Usage(usd=Decimal("0.01")))
    with pytest.raises(ValueError, match="unknown"):
        ledger.reserve("stage-c/req-1", Decimal("0.1"))


# --- Fix 5: evaluation output must not contaminate task sources ------------


def test_evaluate_rejects_output_inside_persisted_task_source(tmp_path):
    from harness_clone.evaluate import evaluate
    from harness_clone.storage import RunStore

    store = RunStore(tmp_path / "run")
    task = make_task(tmp_path)
    store.put("runtime", "tasks", [task.model_dump(mode="json")])
    store.put("runtime", "results", [])
    with pytest.raises(ValueError, match="task source"):
        evaluate(store, {}, task.source_root / "metrics.json")
    with pytest.raises(ValueError, match="task source"):
        evaluate(store, {}, task.source_root / "sub" / "metrics.json")
    report = evaluate(store, {}, tmp_path / "elsewhere" / "metrics.json")
    assert report["tasks"] == 0


# --- Fix 6: parallel proposal contexts are frozen --------------------------


class RecordingEditor(FixtureEditor):
    def __init__(self):
        super().__init__()
        self.decisions_seen = {}

    def set_stage(self, stage, attempt, error, decisions):
        self.decisions_seen[stage.rsplit("/", 1)[-1]] = [dict(d) for d in decisions]


def test_parallel_inv_dom_do_not_see_sibling_stages_but_compose_does(tmp_path):
    from harness_clone.controller import run_task
    from harness_clone.storage import RunStore

    store = RunStore(tmp_path / "runs")
    digest = store.publish_bundle(BASE)
    task = make_task(tmp_path)
    editor = RecordingEditor()
    run_task(task, ExperimentConfig(k=1), store, FixtureExecutor(), editor, digest)
    assert editor.decisions_seen["inv1"] == []
    assert editor.decisions_seen["dom1"] == []
    assert {d["label"] for d in editor.decisions_seen["agg1"]} == {"inv1", "dom1"}


def test_sequential_later_round_sees_earlier_round_stages(tmp_path):
    from harness_clone.controller import run_task
    from harness_clone.storage import RunStore

    store = RunStore(tmp_path / "runs")
    digest = store.publish_bundle(BASE)
    task = make_task(tmp_path)
    editor = RecordingEditor()
    run_task(
        task, ExperimentConfig(mode="sequential", k=2), store, FixtureExecutor(), editor, digest
    )
    assert editor.decisions_seen["inv1"] == []
    assert "c1" in {d["label"] for d in editor.decisions_seen["inv2"]}


# --- Fix 7: immutable runtime image identities -----------------------------


def test_image_identity_mismatch_rejected_and_match_accepted(tmp_path):
    from harness_clone.cli import verify_runtime_images

    config = ExperimentConfig(image_id="sha256:exec1", pi_image_id="sha256:pi1")
    verify_runtime_images(config, lambda tag: "sha256:exec1" if "executor" in tag else "sha256:pi1")
    with pytest.raises(ValueError, match="image"):
        verify_runtime_images(
            config, lambda tag: "sha256:changed" if "executor" in tag else "sha256:pi1"
        )
    # Offline configs without resolved identities skip the check.
    verify_runtime_images(ExperimentConfig(), lambda tag: "sha256:anything")


def test_resume_rejects_changed_runtime_image(tmp_path, monkeypatch):
    import harness_clone.cli as cli
    from harness_clone.storage import RunStore, fingerprint

    config = ExperimentConfig(max_usd=Decimal("1"), image_id="sha256:old", pi_image_id="sha256:pi")
    store = RunStore(tmp_path / "run")
    store.create_experiment(config)
    store.put("runtime", "kind", {"backend": "deepagents-fireworks"})
    task = make_task(tmp_path)
    store.put("runtime", "tasks", [task.model_dump(mode="json")])
    digest = store.publish_bundle(BASE)
    store.put("stream", "signature", {"signature": "x", "learn": True, "base": digest})
    monkeypatch.setattr(cli, "resolve_image_id", lambda tag: "sha256:new")
    with pytest.raises(ValueError, match="image"):
        cli.resume_experiment(fingerprint(config.model_dump(mode="json")), store)


def test_pi_image_tag_comes_from_shared_constant():
    from harness_clone.contracts import PI_IMAGE
    from harness_clone import pi_editor

    assert pi_editor.PI_IMAGE == PI_IMAGE == "harness-clone-pi:0.1"
