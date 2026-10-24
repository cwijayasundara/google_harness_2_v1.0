import importlib.util
import os
import subprocess
from pathlib import Path

import pytest


def test_executor_image_stripping_removes_answer_code_and_bytecode(tmp_path):
    script = Path(__file__).parents[1] / "containers/strip_orderops_evaluation.py"
    spec = importlib.util.spec_from_file_location("strip_orderops", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    package = tmp_path / "site-packages/harness_clone/orderops"
    (package / "__pycache__").mkdir(parents=True)
    for name in [
        "cases.py",
        "score.py",
        "contracts.py",
        "experiment.py",
        "__pycache__/cases.cpython-312.pyc",
        "__pycache__/score.cpython-312.pyc",
        "__pycache__/contracts.cpython-312.pyc",
    ]:
        (package / name).write_text("code")
    module.strip_evaluation(tmp_path)
    assert not (package / "cases.py").exists()
    assert not (package / "score.py").exists()
    assert not (package / "__pycache__/cases.cpython-312.pyc").exists()
    assert not (package / "__pycache__/score.cpython-312.pyc").exists()
    assert (package / "contracts.py").is_file()
    assert (package / "experiment.py").is_file()


def test_direct_and_editor_stages_release_their_actual_budget(tmp_path):
    from decimal import Decimal

    from test_orderops_experiment import Editor, Executor, setup_run

    from harness_clone.contracts import ExperimentConfig, Usage
    from harness_clone.gateway import stage_group
    from harness_clone.orderops.experiment import run_experiment
    from harness_clone.storage import BudgetLedger

    tasks, store, base, _, _, _ = setup_run(tmp_path)
    config = ExperimentConfig(max_usd="2", profile="practical", mode="sequential")
    ledger = BudgetLedger(store.root / "budget.sqlite", config.max_usd)

    def charge(stage):
        group = stage_group(stage)
        ledger.allocate(group, config.stage_usd)
        request = stage + "/request"
        ledger.reserve(request, Decimal("0.01"), allocation_id=group)
        ledger.settle(request, Usage(usd="0.01"))

    class MeteredExecutor(Executor):
        def execute(self, task, bundle_digest, execution_id):
            charge(execution_id)
            return (
                super()
                .execute(task, bundle_digest, execution_id)
                .model_copy(update={"usage": Usage(usd="0.01")})
            )

    class MeteredEditor(Editor):
        def set_stage(self, stage, attempt, error, decisions):
            self.stage = f"{stage}/attempt-{attempt}"

        def choose(self, task, records):
            charge(self.stage)
            return super().choose(task, records)

        def reflect(self, evidence, result):
            charge(self.stage)
            return super().reflect(evidence, result)

    run_experiment(
        tasks,
        config,
        store,
        MeteredExecutor(store),
        lambda task: MeteredEditor(task, []),
        base,
        arms=("frozen", "persistent", "retries"),
    )
    assert ledger.summary()["reserved"] == "0"
    with ledger.transaction() as db:
        allocated = [r[0] for r in db.execute("SELECT id FROM budget WHERE id LIKE 'alloc:%'")]
    retries = [name for name in allocated if "retries-dev-000/rollout-" in name]
    assert len(retries) == 4


@pytest.mark.docker
def test_executor_image_has_no_orderops_answer_generator_or_grader():
    if os.environ.get("HARNESS_TEST_DOCKER") != "1":
        pytest.skip("set HARNESS_TEST_DOCKER=1 after rebuilding the executor image")
    code = """
import importlib.util
from pathlib import Path
assert importlib.util.find_spec('harness_clone.orderops.cases') is None
assert importlib.util.find_spec('harness_clone.orderops.score') is None
for root in ('/app/src', '/app/.venv/lib', '/opt/venv/lib'):
    for package in Path(root).rglob('harness_clone/orderops'):
        for name in ('cases', 'score'):
            assert not (package / (name + '.py')).exists()
            assert not list((package / '__pycache__').glob(name + '.*.pyc'))
"""
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--user=65532:65532",
            "--cap-drop=ALL",
            "harness-clone-executor:0.1",
            "python",
            "-c",
            code,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
