from pathlib import Path

import pytest

from harness_clone.contracts import ExperienceEntry


def test_domain_ordinal_and_namespace_isolation(tmp_path):
    from harness_clone.storage import RunStore
    from harness_clone.experience import ExperienceLibrary

    store = RunStore(tmp_path)
    digest = store.publish_bundle(Path(__file__).parents[1] / "examples/base-harness")
    lib = ExperienceLibrary(store)
    lib.append(
        ExperienceEntry(
            namespace="one",
            domain="code",
            task_id="a",
            ordinal=0,
            bundle_digest=digest,
            cue="code",
            lesson="check output",
            evidence_refs=["run:trace:0"],
        )
    )
    assert lib.snapshot("one", "code", 0).library == []
    assert lib.snapshot("other", "code", 10).library == []
    assert lib.snapshot("one", "legal", 10).library == []
    snap = lib.snapshot("one", "code", 1)
    assert snap.library[0]["bundle_digest"] == digest
    assert (store.bundle_path(digest) / "skills/verify/SKILL.md").is_file()


def test_append_idempotent_and_refuses_conflicting_republication(tmp_path):
    from harness_clone.storage import RunStore
    from harness_clone.experience import ExperienceLibrary

    store = RunStore(tmp_path)
    digest = store.publish_bundle(Path(__file__).parents[1] / "examples/base-harness")
    lib = ExperienceLibrary(store)
    entry = ExperienceEntry(
        namespace="one",
        domain="code",
        task_id="a",
        ordinal=0,
        bundle_digest=digest,
        cue="code",
        lesson="check output",
        evidence_refs=["run:trace:0"],
    )
    assert lib.append(entry) == lib.append(entry)
    with pytest.raises(ValueError):
        lib.append(entry.model_copy(update={"lesson": "changed"}))
