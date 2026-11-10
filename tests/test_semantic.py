"""Semantic audit: lessons judged against the transcripts they cite."""

from pathlib import Path

import pytest

from harness_clone.bundles import bundle_digest
from harness_clone.contracts import ExecutionRecord, ExperienceEntry
from harness_clone.dreaming import build_memory_files, load_entries, run_dream
from harness_clone.experience import ExperienceLibrary
from harness_clone.semantic import SemanticVerdict, verify_semantics
from harness_clone.storage import RunStore

BASE = Path(__file__).parents[1] / "examples/base-harness"
DIGEST = bundle_digest(BASE)

EXEC = "t1/identity/agg1"
REF = f"{EXEC}:trace:t0"


def trace_record(exec_id=EXEC):
    return ExecutionRecord(
        execution_id=exec_id,
        task_id=exec_id.split("/")[0],
        bundle_digest=DIGEST,
        delivered=True,
        trace=[
            {"id": "t0", "type": "tool_call", "content": "ran check"},
            {"id": "t1", "type": "observation", "content": "checkpoint passed on west side"},
        ],
        artifacts={"answer.json": "{}"},
    )


@pytest.fixture
def store(tmp_path):
    run_store = RunStore(tmp_path / "run")
    run_store.publish_bundle(BASE)
    run_store.save_execution(trace_record())
    return run_store


def session_entry(store, cue="checkpoint side", lesson="the checkpoint is on the east side"):
    entry = ExperienceEntry(
        namespace="ns",
        domain="code",
        task_id="t1",
        ordinal=0,
        bundle_digest=DIGEST,
        cue=cue,
        lesson=lesson,
        evidence_refs=[REF],
    )
    ExperienceLibrary(store).append(entry)
    return entry


def config(cap=1.0):
    from harness_clone.contracts import ExperimentConfig

    return ExperimentConfig(max_usd=cap)


def verdict(v, corrected=None, refs=None, reason=""):
    return SemanticVerdict(
        verdict=v,
        corrected_lesson=corrected,
        contradicting_refs=refs or [],
        reason=reason,
    )


def test_contradicted_lesson_is_corrected(store):
    """The Mt. Moon regression: a well-cited wrong lesson gets fixed."""
    entry = session_entry(store)
    corrections, stats = verify_semantics(
        config(),
        store,
        [entry],
        verifier=lambda e, ctx: verdict(
            "contradicted",
            corrected="the checkpoint is on the west side",
            refs=[REF],
        ),
    )
    assert stats.checked == 1 and stats.corrected == 1
    assert len(corrections) == 1
    fix = corrections[0]
    assert fix.origin == "dream" and fix.supersedes == [entry_key_fix(entry)]
    assert fix.verified_on and fix.confidence == "verified"
    assert "west" in fix.lesson


def entry_key_fix(entry):
    from harness_clone.experience import entry_key

    return entry_key(entry)


def test_hallucinated_rebuttal_is_inconclusive_not_guessed(store):
    entry = session_entry(store)
    corrections, stats = verify_semantics(
        config(), store, [entry],
        verifier=lambda e, ctx: verdict(
            "contradicted", corrected="anything", refs=[f"{EXEC}:trace:ghost"]
        ),
    )
    assert corrections == []
    assert stats.inconclusive == 1
    recorded = store.get("semantic-verdict", entry_key_fix(entry))
    assert recorded["reason"] == "rebuttal citations do not resolve in this store"


def test_supported_verdict_upgrades_restatement_confidence(store):
    session_entry(store)
    report = run_dream_with_verifier(store, lambda: verdict("supported"))
    assert (report.semantic_checked, report.semantic_supported) == (1, 1)
    restated = [e for e in load_entries(store) if e.origin == "dream"]
    assert len(restated) == 1
    assert restated[0].confidence == "verified"
    assert restated[0].verified_on


def test_structural_restatement_without_semantics_is_labeled_structural(store):
    session_entry(store)  # no --verify: structural restatement only
    run_dream(store, BASE, adopted_digest=DIGEST)
    restated = [e for e in load_entries(store) if e.origin == "dream"]
    assert len(restated) == 1
    assert restated[0].confidence == "structural"


def run_dream_with_verifier(store, make_verdict):
    """Patch the model seam: every VERIFY call returns make_verdict()."""
    from unittest.mock import patch

    with patch(
        "harness_clone.semantic.run_structured", side_effect=lambda *a, **k: make_verdict()
    ):
        return run_dream(store, BASE, adopted_digest=DIGEST, verify_config=config())


def test_verdicts_persist_second_dream_reverifies_nothing(store):
    entry = session_entry(store)
    calls = []

    def counting(e, ctx):
        calls.append(e.cue)
        return verdict("supported")

    verify_semantics(config(), store, [entry], verifier=counting)
    assert len(calls) == 1
    corrections, stats = verify_semantics(config(), store, [entry], verifier=counting)
    assert corrections == [] and calls == [entry.cue]
    assert stats.checked == 0


def test_secret_shaped_correction_is_dropped(store):
    entry = session_entry(store)
    corrections, stats = verify_semantics(
        config(), store, [entry],
        verifier=lambda e, ctx: verdict(
            "contradicted", corrected="use sk-test-12345678 instead", refs=[REF]
        ),
    )
    assert corrections == []
    assert stats.inconclusive == 1


def test_verifier_failure_counts_as_inconclusive(store):
    entry = session_entry(store)

    def broken(e, ctx):
        raise RuntimeError("gateway down")

    corrections, stats = verify_semantics(config(), store, [entry], verifier=broken)
    assert corrections == []
    assert (stats.checked, stats.inconclusive) == (1, 1)
    assert store.get("semantic-verdict", entry_key_fix(entry))["error"] == "RuntimeError"


def test_semantic_audit_requires_budget(store):
    with pytest.raises(ValueError, match="nonzero spending cap"):
        verify_semantics(config(cap=0.0), store, [])


def test_stale_entries_are_verified_first(store, tmp_path):
    """When capped, verify what likely changed before what likely holds."""
    modified = tmp_path / "modified-bundle"
    modified.mkdir()
    for item in BASE.iterdir():
        target = modified / item.name
        if item.is_dir():
            import shutil

            shutil.copytree(item, target)
        else:
            target.write_text(item.read_text())
    (modified / "system.md").write_text(
        (modified / "system.md").read_text() + "\n# changed\n"
    )
    stale_digest = store.publish_bundle(modified)
    stale = ExperienceEntry(
        namespace="ns", domain="code", task_id="t9", ordinal=1,
        bundle_digest=stale_digest, cue="stale cue", lesson="stale lesson",
        evidence_refs=[REF],
    )
    fresh = session_entry(store)  # ordinal 0, current bundle
    order = []

    def counting(e, ctx):
        order.append(e.cue)
        return verdict("supported")

    verify_semantics(config(), store, [stale, fresh], verifier=counting,
                     adopted_digest=DIGEST)
    assert order[0] == "stale cue"


def test_dream_origin_entries_are_never_reverified(store):
    """Corrections are already consolidated; only fresh lessons are judged."""
    fresh = session_entry(store)
    consolidated = ExperienceEntry(
        **{
            **fresh.model_dump(mode="json"),
            "origin": "dream",
            "revision": 1,
            "verified_on": "2026-11-09",
            "lesson": "already consolidated",
        }
    )
    ExperienceLibrary(store).append(consolidated)
    calls = []

    def counting(e, ctx):
        calls.append(e.cue)
        return verdict("supported")

    verify_semantics(config(), store, [fresh, consolidated], verifier=counting)
    assert calls == [fresh.cue]


def test_oversized_wiki_page_splits_and_keeps_every_lesson(store):
    """Phase 1.5: pages fit the injected budget; lessons are never cut."""
    lib = ExperienceLibrary(store)
    for i in range(6):
        entry = ExperienceEntry(
            namespace="ns", domain="code", task_id=f"t{i}", ordinal=i,
            bundle_digest=DIGEST, cue=f"cue {i}",
            lesson="long lesson " * 40 + str(i),  # ~500 chars each
            evidence_refs=[REF],
        )
        lib.append(entry)
    entries = load_entries(store)
    files = build_memory_files(
        entries, BASE, adopted_digest=DIGEST, retrieval_limit=5,
        memory_budget_tokens=512,  # ~2048 chars: forces a split
    )
    pages = [rel for rel in files if rel != "memory/AGENTS.md"]
    assert len(pages) >= 2
    # Every lesson survives in some part, none truncated.
    all_text = "\n".join(files[rel] for rel in pages)
    for i in range(6):
        assert "long lesson " * 40 + str(i) in all_text
    # The index links every part.
    index = files["memory/AGENTS.md"]
    for rel in pages:
        assert rel.split("/")[1].removesuffix(".md") in index


def test_select_memory_pages_budget_and_count(tmp_path):
    """Phase 1.5: the compiler's injected layer respects both caps."""
    from harness_clone.compiler import select_memory_pages
    from harness_clone.contracts import HarnessConfig

    tuning = HarnessConfig(memory_budget_tokens=1000, retrieval_limit=3)
    def page(name, chars):
        p = tmp_path / name
        p.write_text("x" * chars)
        return p

    agents = page("AGENTS.md", 400)  # index: 400 chars = 100 tokens
    a = page("a.md", 1200)           # fits: 400+1200=1600 <= 4000
    b = page("b.md", 3000)           # would blow the 4000-char budget
    c = page("c.md", 400)            # fits after a
    selected, skipped = select_memory_pages([b, a, c, agents], tuning)
    assert selected == [agents, a, c]
    assert skipped == [b]
