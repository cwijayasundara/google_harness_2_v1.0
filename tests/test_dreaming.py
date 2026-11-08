"""Offline dreaming: audit, consolidation, and the memory wiki patch."""

import json
from pathlib import Path

import pytest

from harness_clone.bundles import bundle_digest
from harness_clone.contracts import ExecutionRecord, ExperienceEntry
from harness_clone.dreaming import (
    audit_library,
    build_memory_files,
    consolidate,
    entry_key,
    load_entries,
    run_dream,
)
from harness_clone.experience import ExperienceLibrary
from harness_clone.storage import RunStore

BASE = Path(__file__).parents[1] / "examples/base-harness"


def make_entry(store, lib, digest, *, task_id="a", ordinal=0, cue="code", lesson="check output",
               refs=None, namespace="ns", domain="code", **kwargs):
    entry = ExperienceEntry(
        namespace=namespace,
        domain=domain,
        task_id=task_id,
        ordinal=ordinal,
        bundle_digest=digest,
        cue=cue,
        lesson=lesson,
        evidence_refs=refs or ["missing-exec:trace:0"],
        **kwargs,
    )
    lib.append(entry)
    return entry


@pytest.fixture
def store(tmp_path):
    run_store = RunStore(tmp_path / "run")
    run_store.publish_bundle(BASE)
    return run_store


@pytest.fixture
def lib(store):
    return ExperienceLibrary(store)


def digest_of(store):
    return bundle_digest(BASE)


def verified_refs(store, digest):
    """Publish one execution record and return resolvable refs to it."""
    execution_id = "task-a/identity/agg1"
    store.save_execution(
        ExecutionRecord(
            execution_id=execution_id,
            task_id="task-a",
            bundle_digest=digest,
            delivered=True,
            trace=[{"id": "t0", "type": "tool_call", "content": "ran check"}],
            artifacts={"answer.json": "{}"},
        )
    )
    return [f"{execution_id}:trace:t0", f"{execution_id}:artifact:answer.json"]


def test_snapshot_stops_serving_superseded_entries(store, lib):
    digest = digest_of(store)
    original = make_entry(store, lib, digest, refs=["x:trace:0"])
    assert lib.snapshot("ns", "code", 1).library

    restatement = ExperienceEntry(
        **{**original.model_dump(mode="json"), "origin": "dream", "revision": 1,
           "verified_on": "2026-11-08", "supersedes": [entry_key(original)],
           "lesson": "check output twice"},
    )
    lib.append(restatement)
    served = lib.snapshot("ns", "code", 1).library
    assert len(served) == 1
    assert served[0]["lesson"] == "check output twice"
    assert served[0]["verified_on"] == "2026-11-08"
    # The superseded stub remains in the store as an audit record.
    assert len(load_entries(store)) == 2


def test_audit_classifies_verified_broken_duplicate_and_conflict(store, lib):
    digest = digest_of(store)
    refs = verified_refs(store, digest)
    make_entry(store, lib, digest, task_id="a", refs=refs)
    make_entry(store, lib, digest, task_id="b", cue="solo", lesson="unique", refs=["ghost:trace:0"])
    make_entry(store, lib, digest, task_id="c", cue="dup", lesson="same", refs=refs)
    make_entry(store, lib, digest, task_id="d", cue="dup", lesson="same", refs=refs)
    make_entry(store, lib, digest, task_id="e", cue="split", lesson="one way", refs=refs)
    make_entry(store, lib, digest, task_id="f", cue="split", lesson="another way", refs=refs)
    audit = audit_library(store, load_entries(store), adopted_digest=digest)
    by_status = {}
    for finding in audit.findings:
        by_status.setdefault(finding.status, []).append(finding)
    # Two levels: per-entry verification (a, c, d, e, f resolve; b does not)
    # plus group findings for the shared-cue clusters.
    assert len(by_status["verified"]) == 5
    assert len(by_status["broken-ref"]) == 1
    assert len(by_status["duplicate"]) == 2
    assert len(by_status["conflict"]) == 2


def test_consolidate_restates_merges_and_marks_conflicts(store, lib):
    digest = digest_of(store)
    refs = verified_refs(store, digest)
    good = make_entry(store, lib, digest, task_id="a", refs=refs)
    dup1 = make_entry(store, lib, digest, task_id="c", cue="dup", lesson="same", refs=refs)
    dup2 = make_entry(store, lib, digest, task_id="d", cue="dup", lesson="same", refs=refs)
    conflict1 = make_entry(store, lib, digest, task_id="e", cue="split", lesson="one way", refs=refs)
    conflict2 = make_entry(
        store, lib, digest, task_id="f", cue="split", lesson="another way",
        refs=["ghost:trace:0"], ordinal=4,
    )
    entries = load_entries(store)
    audit = audit_library(store, entries, adopted_digest=digest)
    dream = consolidate(store, entries, audit)
    # One restatement (verified original), one merge, one conflict note.
    assert len(dream) == 3
    restated = [e for e in dream if not e.lesson.startswith("CONFLICT")]
    conflict_notes = [e for e in dream if e.lesson.startswith("CONFLICT")]
    assert len(restated) == 2 and len(conflict_notes) == 1
    restatement_of_good = next(e for e in restated if e.cue == "code")
    assert restatement_of_good.verified_on and restatement_of_good.origin == "dream"
    assert restatement_of_good.supersedes == [entry_key(good)]
    merge = next(e for e in restated if e.cue == "dup")
    assert set(merge.supersedes) == {entry_key(dup1), entry_key(dup2)}
    note = conflict_notes[0]
    assert "CONFLICT" in note.lesson and "one way" in note.lesson and "another way" in note.lesson
    assert {entry_key(conflict1), entry_key(conflict2)} <= set(note.supersedes)
    # The note must stay visible to every later task in the domain.
    assert note.ordinal == min(conflict1.ordinal, conflict2.ordinal)
    # Snapshots now serve only the consolidated entries.
    served = lib.snapshot("ns", "code", 10).library
    assert len(served) == 3
    assert all(not e["supersedes"] or e["origin"] == "dream" for e in served)


def test_consolidate_keeps_later_correction_and_notes_what_it_replaced(store, lib):
    digest = digest_of(store)
    refs = verified_refs(store, digest)
    old1 = make_entry(store, lib, digest, task_id="day-1", ordinal=1,
                      cue="test-run", lesson="run make test with FOO=1", refs=refs)
    old2 = make_entry(store, lib, digest, task_id="day-2", ordinal=2,
                      cue="test-run", lesson="run make test with FOO=1", refs=refs)
    corrected = make_entry(store, lib, digest, task_id="day-3", ordinal=3,
                           cue="test-run", lesson="run make test plain", refs=refs)
    entries = load_entries(store)
    audit = audit_library(store, entries, adopted_digest=digest)
    dream = consolidate(store, entries, audit)
    assert len(dream) == 1
    note = dream[0]
    assert note.lesson.startswith("run make test plain")
    assert 'replaces "run make test with FOO=1"' in note.lesson
    assert "day-1" in note.lesson and note.verified_on
    assert set(note.supersedes) == {entry_key(old1), entry_key(old2), entry_key(corrected)}
    served = lib.snapshot("ns", "code", 10).library
    assert len(served) == 1 and served[0]["lesson"].startswith("run make test plain")


def test_consolidate_leaves_broken_refs_and_dream_entries_alone(store, lib):
    digest = digest_of(store)
    make_entry(store, lib, digest, task_id="a", refs=["ghost:trace:0"])
    entries = load_entries(store)
    audit = audit_library(store, entries, adopted_digest=digest)
    assert consolidate(store, entries, audit) == []
    # A second dream over its own output does not loop.
    make_entry(store, lib, digest, task_id="b", refs=verified_refs(store, digest))
    entries = load_entries(store)
    audit = audit_library(store, entries, adopted_digest=digest)
    dream = consolidate(store, entries, audit)
    assert len(dream) == 1
    entries = load_entries(store)
    audit = audit_library(store, entries, adopted_digest=digest)
    assert consolidate(store, entries, audit) == []


def test_wiki_keeps_standing_text_caps_files_and_drops_secrets(store, lib):
    digest = digest_of(store)
    refs = verified_refs(store, digest)
    make_entry(store, lib, digest, task_id="a", refs=refs, domain="portfolio",
               lesson="trim positions above the 20% cap")
    make_entry(store, lib, digest, task_id="b", refs=refs, domain="ops",
               lesson="the deploy token is api_key=supersecret123")
    for domain in [f"d{i}" for i in range(6)]:
        make_entry(store, lib, digest, task_id="x", domain=domain, cue=domain, refs=refs)
    files = build_memory_files(
        load_entries(store), BASE, adopted_digest=digest, retrieval_limit=5
    )
    assert "memory/AGENTS.md" in files
    names = sorted(k for k in files if k != "memory/AGENTS.md")
    assert names == sorted(names)
    # retrieval_limit=5: the index plus four detail files at most.
    assert len(files) <= 5
    agents = files["memory/AGENTS.md"]
    assert "Prior examples do not establish facts" in agents  # standing text preserved
    assert agents.index("Prior examples do not establish facts") < agents.index("dream:index:start")
    assert "<!-- dream:index:start -->" in agents and "<!-- dream:index:end -->" in agents
    portfolio_file = next(v for k, v in files.items() if "portfolio" in k)
    assert portfolio_file.startswith("---\n")
    assert "20% cap" in portfolio_file
    # Provenance is explicit even before consolidation restates the entry.
    assert "unverified; task a; bundle " in portfolio_file
    assert all("supersecret123" not in body for body in files.values())
    # Provenance is explicit: how the rule is known and from which bundle.
    assert "bundle " + digest[:12] in portfolio_file


def test_wiki_notes_staleness_against_adopted_bundle(store, lib):
    digest = digest_of(store)
    make_entry(store, lib, digest, task_id="a", refs=["x:trace:0"])
    files = build_memory_files(
        load_entries(store), BASE, adopted_digest="f" * 64, retrieval_limit=5
    )
    body = next(v for k, v in files.items() if k != "memory/AGENTS.md")
    assert f"stale: adopted bundle is {'f' * 12}" in body


def test_run_dream_publishes_valid_patch_and_converges(store):
    digest = digest_of(store)
    lib = ExperienceLibrary(store)
    refs = verified_refs(store, digest)
    make_entry(store, lib, digest, task_id="a", refs=refs)
    make_entry(store, lib, digest, task_id="b", refs=refs)  # duplicate lesson
    report = run_dream(store, BASE, adopted_digest=digest, retrieval_limit=5)
    assert report.parent_digest == digest
    assert report.patch_digest
    # One merge entry superseding both duplicate members.
    assert report.restated == 1 and report.merged == 1
    assert not report.adopted
    # The published patch is a real bundle in the store, memory-only.
    candidate = Path(store.bundle_path(report.patch_digest))
    assert bundle_digest(candidate) == report.patch_digest
    changed = set()
    for rel in ("memory/AGENTS.md",):
        changed.add(rel)
    assert "memory/AGENTS.md" in report.wiki_files
    # Second dream: nothing left to restate, same wiki, same digest.
    again = run_dream(store, BASE, adopted_digest=digest, retrieval_limit=5)
    assert again.restated == 0 and again.merged == 0
    assert again.patch_digest == report.patch_digest


def test_run_dream_empty_library_makes_no_patch(store):
    report = run_dream(store, BASE, adopted_digest=digest_of(store))
    assert report.audit.entries == 0
    assert report.patch_digest is None
    assert report.wiki_files == []


def test_cli_dream(tmp_path, capsys):
    from harness_clone.cli import main

    run_dir = tmp_path / "run"
    store = RunStore(run_dir)
    digest = store.publish_bundle(BASE)
    lib = ExperienceLibrary(store)
    refs = verified_refs(store, digest)
    make_entry(store, lib, digest, task_id="a", refs=refs)

    code = main(["dream", "--run-dir", str(run_dir), "--base-harness", str(BASE)])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["patch_digest"]
    assert report["audit"]["entries"] == 1
    assert (Path(store.bundle_path(report["patch_digest"])) / "memory/AGENTS.md").is_file()
