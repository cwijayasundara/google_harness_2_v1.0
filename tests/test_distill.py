"""Live distillation: analyzer extracts lessons from full transcripts."""

from pathlib import Path

import pytest

from harness_clone.bundles import bundle_digest
from harness_clone.contracts import ExecutionRecord, ExperienceEntry
from harness_clone.distill import DistillDecision, DistilledObservation, analyze_transcripts
from harness_clone.dreaming import load_entries, run_dream
from harness_clone.experience import ExperienceLibrary
from harness_clone.storage import RunStore

EXEC = "t1/identity/agg1"
BASE = Path(__file__).parents[1] / "examples/base-harness"
DIGEST = bundle_digest(BASE)


def trace_record(exec_id=EXEC, trace=None, artifacts=None):
    return ExecutionRecord(
        execution_id=exec_id,
        task_id=exec_id.split("/")[0],
        bundle_digest=DIGEST,
        delivered=True,
        trace=trace or [{"id": "t0", "type": "tool_call", "content": "ran check"}],
        artifacts=artifacts if artifacts is not None else {"answer.json": "{}"},
    )


@pytest.fixture
def store(tmp_path):
    run_store = RunStore(tmp_path / "run")
    run_store.publish_bundle(BASE)
    run_store.put("stream", "task-domains", {"t1": "code"})
    return run_store


def seed_session_lesson(store, verified_on=None):
    """One session entry, so the distiller can resolve a namespace."""
    entry = ExperienceEntry(
        namespace="ns",
        domain="code",
        task_id="t0",
        ordinal=0,
        bundle_digest=DIGEST,
        cue="existing",
        lesson="known lesson",
        evidence_refs=[f"{EXEC}:trace:t0"],
        verified_on=verified_on,
    )
    ExperienceLibrary(store).append(entry)
    return entry


def config(cap=1.0):
    from harness_clone.contracts import ExperimentConfig

    return ExperimentConfig(max_usd=cap)


def observes(refs=None, cue="fallbacks", lesson="retry once, then fall back"):
    return DistillDecision(
        execution_id=EXEC,
        observation=DistilledObservation(
            cue=cue,
            lesson=lesson,
            evidence_refs=refs or [f"{EXEC}:trace:t0"],
        ),
    )


def test_distill_appends_resolvable_observation(store):
    seed = seed_session_lesson(store)
    store.save_execution(trace_record())
    fresh, stats = analyze_transcripts(config(), store, [seed], analyzer=lambda ctx: observes())
    assert (stats.analyzed, stats.distilled, stats.skipped) == (1, 1, 0)
    assert len(fresh) == 1
    entry = fresh[0]
    assert entry.origin == "distilled"
    assert entry.domain == "code"
    # Hindsight lands past every existing entry: visible to later tasks only.
    assert entry.ordinal == 1
    assert entry.evidence_refs == [f"{EXEC}:trace:t0"]


def test_distill_rejects_hallucinated_citations(store):
    seed = seed_session_lesson(store)
    store.save_execution(trace_record())
    fresh, stats = analyze_transcripts(
        config(), store, [seed],
        analyzer=lambda ctx: observes(refs=[f"{EXEC}:trace:ghost-row"]),
    )
    assert fresh == []
    assert (stats.analyzed, stats.distilled, stats.skipped) == (1, 0, 1)
    recorded = store.get("distilled", EXEC)
    assert recorded["reason"] == "citations do not resolve in this transcript"


def test_distill_will_not_store_credentials(store):
    seed = seed_session_lesson(store)
    store.save_execution(trace_record())
    fresh, stats = analyze_transcripts(
        config(), store, [seed],
        analyzer=lambda ctx: observes(lesson="use sk-test-1234 for retries"),
    )
    assert fresh == []
    assert stats.skipped == 1
    assert "credential" in store.get("distilled", EXEC)["reason"]


def test_distill_records_no_observation_reason(store):
    seed = seed_session_lesson(store)
    store.save_execution(trace_record())
    fresh, stats = analyze_transcripts(
        config(), store, [seed],
        analyzer=lambda ctx: DistillDecision(execution_id=EXEC, observation=None,
                                             reason="nothing to learn"),
    )
    assert fresh == []
    assert (stats.analyzed, stats.distilled, stats.skipped) == (1, 0, 0)
    assert store.get("distilled", EXEC)["reason"] == "nothing to learn"


def test_distill_counts_analyzer_failures(store):
    seed = seed_session_lesson(store)
    store.save_execution(trace_record())

    def broken(context):
        raise RuntimeError("gateway down")

    fresh, stats = analyze_transcripts(config(), store, [seed], analyzer=broken)
    assert fresh == []
    assert stats.skipped == 1
    assert store.get("distilled", EXEC)["error"] == "RuntimeError"


def test_distill_second_run_sees_no_new_input(store):
    seed = seed_session_lesson(store)
    store.save_execution(trace_record())
    calls = []

    def counting(context):
        calls.append(context["transcript"]["execution_id"])
        return observes()

    analyze_transcripts(config(), store, [seed], analyzer=counting)
    assert calls == [EXEC]
    fresh, stats = analyze_transcripts(config(), store, [seed], analyzer=counting)
    assert fresh == []
    assert (stats.analyzed, stats.distilled, stats.skipped) == (0, 0, 0)


def test_distill_requires_nonzero_budget(store):
    with pytest.raises(ValueError, match="nonzero spending cap"):
        analyze_transcripts(config(cap=0.0), store, [])


def test_dream_with_distill_restates_distilled_lesson(store):
    """End to end: distill feeds the deterministic pass, which verifies it.

    The real analyzer needs a model; the deterministic pipeline around it
    must not, so the model seam is patched and the rest runs for real.
    """
    from unittest.mock import patch

    # Already verified, so only the distilled lesson is restated below.
    seed_session_lesson(store, verified_on="2026-11-07")
    store.save_execution(trace_record())
    with patch("harness_clone.distill.run_structured", return_value=observes()):
        report = run_dream(store, BASE, adopted_digest=DIGEST, distill_config=config())
    assert (report.analyzed, report.distilled, report.distill_skipped) == (1, 1, 0)
    assert report.restated == 1
    dream_entries = [e for e in load_entries(store) if e.origin == "dream"]
    assert len(dream_entries) == 1
    assert dream_entries[0].verified_on
    assert dream_entries[0].lesson.startswith("retry once")


def test_dream_without_distill_flag_never_calls_the_model(store):
    seed_session_lesson(store, verified_on="2026-11-07")
    store.save_execution(trace_record())
    report = run_dream(store, BASE, adopted_digest=DIGEST)
    assert (report.analyzed, report.distilled, report.distill_skipped) == (0, 0, 0)


def test_distill_skips_unattributable_transcripts(store):
    """No stream map, no matching entry: skip with a reason, never guess."""
    store.put("stream", "task-domains", {})
    seed = seed_session_lesson(store)  # belongs to task t0, not t1
    store.save_execution(trace_record())
    fresh, stats = analyze_transcripts(config(), store, [seed], analyzer=lambda ctx: observes())
    assert fresh == []
    assert (stats.analyzed, stats.distilled, stats.skipped) == (1, 0, 1)
    assert "no domain" in store.get("distilled", EXEC)["reason"]
