from pathlib import Path

import pytest

from harness_clone.contracts import HarnessPatch


@pytest.mark.parametrize("mode", ["parallel", "sequential"])
def test_mvp_deterministic_two_task_stream(tmp_path, mode):
    from harness_clone.demo import run_demo
    from harness_clone.storage import RunStore

    report = run_demo(tmp_path, mode=mode, k=2)
    assert report["tasks"] == 2 and report["executions"] == 16
    store = RunStore(tmp_path)
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM records WHERE kind='experience'").fetchone()[0] == 2
    assert run_demo(tmp_path, mode=mode, k=2) == report


@pytest.mark.parametrize(
    "rel,content",
    [
        ("system.md", "Check every output and use verify."),
        ("guardrails.md", "Verify sources and use verify."),
        ("memory/AGENTS.md", "Check counts against supplied data."),
        ("tools/descriptions.json", '{"execute":"Run a bounded check and inspect its exit code."}'),
        (
            "subagents/reviewer.json",
            '{"name":"reviewer","description":"Review totals","system_prompt":"Check count and total."}',
        ),
        (
            "skills/verify/SKILL.md",
            "---\nname: verify\ndescription: Verify totals\n---\nInspect every requested output.",
        ),
        ("scripts/check.py", 'print("checked")\n'),
        (
            "middleware/audit.py",
            "from langchain.agents.middleware import AgentMiddleware\ndef create_middleware(config): return AgentMiddleware()\n",
        ),
        (
            "config.json",
            '{"context_tokens":16384,"compaction_fraction":0.6,"retrieval_limit":3,"delegation_depth":0}',
        ),
    ],
)
def test_every_component_can_be_versioned_independently(tmp_path, rel, content):
    from harness_clone.bundles import bundle_digest, apply_patch

    parent = Path(__file__).parents[2] / "examples/base-harness"
    digest = bundle_digest(parent)
    child = apply_patch(
        parent, HarnessPatch(parent_digest=digest, upserts={rel: content}), tmp_path / "candidate"
    )
    assert child != digest
    assert (tmp_path / "candidate" / rel).read_text() == content
