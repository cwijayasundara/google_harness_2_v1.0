"""Phase 2: link repair (free) and the organizer pass (metered)."""

import pytest
from pathlib import Path

from harness_clone.bundles import bundle_digest
from harness_clone.contracts import ExecutionRecord, ExperienceEntry
from harness_clone.dreaming import build_memory_files, load_entries, run_dream
from harness_clone.experience import ExperienceLibrary
from harness_clone.organize import OrganizedPage, organize_wiki, repair_links
from harness_clone.storage import RunStore

BASE = Path(__file__).parents[1] / "examples/base-harness"
DIGEST = bundle_digest(BASE)
REF = "t1/identity/agg1:trace:t0"


def config(cap=1.0):
    from harness_clone.contracts import ExperimentConfig

    return ExperimentConfig(max_usd=cap)


@pytest.fixture
def store(tmp_path):
    run_store = RunStore(tmp_path / "run")
    run_store.publish_bundle(BASE)
    run_store.save_execution(
        ExecutionRecord(
            execution_id="t1/identity/agg1",
            task_id="t1",
            bundle_digest=DIGEST,
            delivered=True,
            trace=[{"id": "t0", "type": "tool_call", "content": "ran"}],
        )
    )
    lib = ExperienceLibrary(run_store)
    for domain, lesson in [
        ("code", "the checkpoint is on the east side"),
        ("ops", "retry the deploy once before paging"),
    ]:
        lib.append(
            ExperienceEntry(
                namespace="ns",
                domain=domain,
                task_id="t1",
                ordinal=0,
                bundle_digest=DIGEST,
                cue=f"cue for {domain}",
                lesson=lesson,
                evidence_refs=[REF],
            )
        )
    return run_store


def pages(store):
    return build_memory_files(
        load_entries(store), BASE, adopted_digest=DIGEST
    )


def test_link_repair_repoints_normalized_alias():
    files = {
        "memory/AGENTS.md": "index [[code-checkpoint]]",
        "memory/code-checkpoint.md": "see [[Code_Checkpoint]] and [[OPS]]",
        "memory/ops.md": "plain",
    }
    stats = repair_links(files)
    assert "see [[code-checkpoint]] and [[ops]]" in files["memory/code-checkpoint.md"]
    assert stats.repaired == 2 and stats.dropped == 0


def test_link_repair_drops_dangling_links_but_keeps_text():
    files = {
        "memory/AGENTS.md": "x",
        "memory/ops.md": "the [[ghost-page]] note stays readable",
    }
    stats = repair_links(files)
    assert files["memory/ops.md"] == "the ghost-page note stays readable"
    assert stats.dropped == 1


def test_link_repair_is_idempotent():
    files = {
        "memory/AGENTS.md": "x",
        "memory/ops.md": "[[OPS]] and [[missing]]",
    }
    repair_links(files)
    once = files["memory/ops.md"]
    stats = repair_links(files)
    assert files["memory/ops.md"] == once
    assert stats.repaired == 0 and stats.dropped == 0


def test_link_repair_leaves_standing_text_alone(store):
    files = pages(store)
    index = files["memory/AGENTS.md"]
    files["memory/AGENTS.md"] = (
        "Hand-written [[Something]] stays\n" + index
    )
    repair_links(files)
    assert "Hand-written [[Something]] stays" in files["memory/AGENTS.md"]


def test_organizer_rewrites_page_and_stays_in_it(store):
    files = pages(store)
    ops_page = next(name for name in files if "ops" in name)

    def organizer(context):
        # Each call must carry its own page's raw body.
        stem = "ops" if "retry the deploy" in context["page"] else "code"
        return OrganizedPage(
            rewritten=f"## {stem} rewritten\n\nLoad-bearing: [[code]] owns the checkpoint side.",
            note="grouped",
        )

    index_before = files["memory/AGENTS.md"]
    rewritten, stats = organize_wiki(config(), store, files, organizer=organizer)
    n_pages = len([n for n in files if n != "memory/AGENTS.md"])
    assert stats.organized == n_pages
    assert "## ops rewritten" in rewritten[ops_page]
    other = next(name for name in files if "code" in name and name != ops_page)
    assert "## code rewritten" in rewritten[other]
    assert files["memory/AGENTS.md"] == index_before  # the index is never rewritten


def test_organizer_preserves_frontmatter(store):
    files = pages(store)
    target = next(name for name in files if name != "memory/AGENTS.md")
    organize_wiki(
        config(), store, files,
        organizer=lambda ctx: OrganizedPage(rewritten="## tidy"),
    )
    assert files[target].startswith("---\n")
    assert "name: " in files[target]


def test_organizer_secret_rewrite_is_skipped(store):
    files = pages(store)
    target = next(name for name in files if name != "memory/AGENTS.md")
    raw = files[target]
    rewritten, stats = organize_wiki(
        config(), store, files,
        organizer=lambda ctx: OrganizedPage(rewritten="use sk-test-12345678 please"),
    )
    assert rewritten[target] == raw
    n_pages = len([n for n in files if n != "memory/AGENTS.md"])
    assert stats.skipped == n_pages and stats.organized == 0


def test_organizer_failure_is_recorded_and_skipped(store):
    files = pages(store)
    target = next(name for name in files if name != "memory/AGENTS.md")
    raw = files[target]

    def broken(ctx):
        raise RuntimeError("gateway down")

    rewritten, stats = organize_wiki(config(), store, files, organizer=broken)
    assert rewritten[target] == raw
    assert stats.skipped == len([n for n in files if n != "memory/AGENTS.md"])
    recorded = [
        store.get("organized", key)
        for key in _organized_keys(store)
        if "error" in (store.get("organized", key) or {})
    ]
    assert recorded and recorded[0]["error"] == "RuntimeError"


def _organized_keys(store):
    with store.transaction() as db:
        rows = db.execute("SELECT id FROM records WHERE kind='organized'").fetchall()
    return [row[0] for row in rows]


def test_organizer_empty_rewrite_is_skipped(store):
    files = pages(store)
    n_pages = len([n for n in files if n != "memory/AGENTS.md"])
    rewritten, stats = organize_wiki(
        config(), store, files,
        organizer=lambda ctx: OrganizedPage(rewritten="   "),
    )
    assert stats.skipped == n_pages and stats.organized == 0


def test_organizer_requires_budget(store):
    files = pages(store)
    with pytest.raises(ValueError, match="nonzero spending cap"):
        organize_wiki(config(cap=0.0), store, files)


def test_second_dream_reuses_cached_rewrite_and_calls_nothing(store):
    files = pages(store)
    raw = dict(files)  # what a second dream rebuilds: identical raw pages
    n_pages = len([n for n in files if n != "memory/AGENTS.md"])
    calls = []

    def organizer(ctx):
        calls.append(1)
        return OrganizedPage(rewritten="## organized once")

    organize_wiki(config(), store, files, organizer=organizer)
    assert len(calls) == n_pages

    # A second pass over the same raw pages reuses the cached rewrite:
    # the organizer (which would raise) is never called.
    def refusing(ctx):
        raise AssertionError("organizer called on unchanged entries")

    rebuilt = dict(raw)
    rewritten, stats = organize_wiki(config(), store, rebuilt, organizer=refusing)
    target = next(name for name in files if name != "memory/AGENTS.md")
    assert stats.cached == n_pages
    assert "organized once" in rewritten[target]


def test_run_dream_with_organize_reports_counters_and_repairs_links(store):
    from unittest.mock import patch

    def fake_run_structured(config, st, role, context, schema, stage_id):
        return OrganizedPage(
            rewritten="## grouped\n\nSee [[OPS]] and also [[nowhere]]."
        )

    with patch(
        "harness_clone.organize.run_structured", side_effect=fake_run_structured
    ):
        report = run_dream(store, BASE, adopted_digest=DIGEST, organize_config=config())
    assert report.organized >= 1
    # Every page rewritten to the same body: [[OPS]] repointed per page,
    # [[nowhere]] dropped per page; deterministic and free.
    n_pages = report.wiki_files.__len__() - 1
    assert report.links_repaired == n_pages
    assert report.links_dropped == n_pages
    assert report.patch_digest


def test_free_dream_still_repairs_links_without_model_calls(store):
    lib = ExperienceLibrary(store)
    lib.append(
        ExperienceEntry(
            namespace="ns", domain="ops", task_id="t2", ordinal=1,
            bundle_digest=DIGEST, cue="cue", lesson="learned [[OPS]] aliasing",
            evidence_refs=[REF],
        )
    )
    report = run_dream(store, BASE, adopted_digest=DIGEST)
    assert report.organized == 0 and report.organizer_skipped == 0
    assert report.links_repaired >= 1  # model-authored alias normalized, free
