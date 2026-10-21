from pathlib import Path

import pytest

from harness_clone.contracts import ExecutionRecord, ExperimentConfig


def store(tmp_path):
    from harness_clone.storage import RunStore

    return RunStore(tmp_path)


def test_resume_rejects_changed_model_or_library_snapshot(tmp_path):
    s = store(tmp_path)
    s.create_experiment(ExperimentConfig())
    with pytest.raises(ValueError, match="configuration"):
        s.create_experiment(ExperimentConfig(library_snapshot="other"))


def test_identical_bundle_different_draws_are_not_deduplicated(tmp_path):
    s = store(tmp_path)
    digest = s.publish_bundle(Path(__file__).parents[1] / "examples/base-harness")
    for draw in ["base1", "base2"]:
        s.save_execution(
            ExecutionRecord(
                execution_id=draw,
                task_id="one",
                bundle_digest=digest,
                delivered=True,
                artifacts={"answer.txt": draw},
            )
        )
    assert s.completed_execution("base1").artifacts == {"answer.txt": "base1"}
    assert s.completed_execution("base2").artifacts == {"answer.txt": "base2"}
    (s.bundle_path(digest) / "system.md").write_text("tampered")
    with pytest.raises(ValueError, match="digest"):
        s.completed_execution("base1")


def test_partial_publication_never_appears_complete(tmp_path, monkeypatch):
    s = store(tmp_path)
    source = Path(__file__).parents[1] / "examples/base-harness"
    from harness_clone.bundles import bundle_digest

    digest = bundle_digest(source)
    real = Path.rename

    def fail(self, target):
        raise OSError("simulated crash")

    monkeypatch.setattr(Path, "rename", fail)
    with pytest.raises(OSError):
        s.publish_bundle(source)
    assert not s.bundle_path(digest).exists()
    monkeypatch.setattr(Path, "rename", real)
    assert s.publish_bundle(source) == digest


def test_incomplete_execution_and_content_changes_not_reused(tmp_path):
    s = store(tmp_path)
    d = s.publish_bundle(Path(__file__).parents[1] / "examples/base-harness")
    s.save_execution(
        ExecutionRecord(execution_id="one", task_id="t", bundle_digest=d, status="unknown")
    )
    assert s.completed_execution("one") is None
    s.save_execution(
        ExecutionRecord(execution_id="one", task_id="t", bundle_digest=d, artifacts={"x": "ok"})
    )
    with pytest.raises(ValueError):
        s.save_execution(
            ExecutionRecord(
                execution_id="one", task_id="t", bundle_digest=d, artifacts={"x": "changed"}
            )
        )
