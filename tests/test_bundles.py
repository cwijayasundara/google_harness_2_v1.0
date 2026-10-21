import json
import shutil
from pathlib import Path

import pytest


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "bundle"
    shutil.copytree(Path(__file__).parents[1] / "examples/base-harness", root)
    return root


def api():
    from harness_clone import bundles, contracts
    return bundles, contracts


def test_bundle_covers_all_component_categories(bundle):
    b, c = api()
    m = b.validate_bundle(bundle)
    assert set(m.components) == {"system", "guardrails", "memory", "tools", "subagents", "skills", "scripts", "middleware"}
    digest = b.bundle_digest(bundle)
    patch = c.HarnessPatch(parent_digest=digest, upserts={"system.md": "Changed instructions; use verify"})
    dest = bundle.parent / "next"
    assert b.apply_patch(bundle, patch, dest) != digest
    assert (bundle / "system.md").read_text() != "Changed instructions"
    assert (dest / "system.md").read_text() == "Changed instructions; use verify"


@pytest.mark.parametrize("path", ["../escape", "/tmp/escape", "skills/../../escape", "other.txt"])
def test_patch_rejects_unsafe_paths(bundle, path):
    b, c = api()
    with pytest.raises(ValueError):
        b.apply_patch(bundle, c.HarnessPatch(parent_digest=b.bundle_digest(bundle), upserts={path: "bad"}), bundle.parent / "next")
    assert not (bundle.parent / "next").exists()


@pytest.mark.parametrize("path,content", [
    ("skills/check/SKILL.md", "No frontmatter"),
    ("scripts/check.py", "def invalid("),
    ("subagents/check.json", '{"name":"check"}'),
    ("config.json", '{"compaction_fraction":1.5}'),
    ("config.json", '{"unknown_option":true}'),
    ("system.md", "Run scripts/missing.py before finishing"),
])
def test_invalid_components_rejected(bundle, path, content):
    b, _ = api()
    dest = bundle / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content)
    with pytest.raises(ValueError):
        b.validate_bundle(bundle)


def test_symlinks_and_parent_mismatch_rejected(bundle):
    b, c = api()
    (bundle / "memory/escape").symlink_to("/etc/passwd")
    with pytest.raises(ValueError):
        b.validate_bundle(bundle)
    (bundle / "memory/escape").unlink()
    with pytest.raises(ValueError, match="parent"):
        b.apply_patch(bundle, c.HarnessPatch(parent_digest="0" * 64), bundle.parent / "next")


def test_patch_conflicts_and_config_limits(bundle):
    b, c = api()
    with pytest.raises(ValueError):
        c.HarnessPatch(parent_digest="0" * 64, upserts={"system.md":"x"}, deletions=["system.md"])
    (bundle / "config.json").write_text(json.dumps({"context_tokens": 500000}))
    with pytest.raises(ValueError):
        b.validate_bundle(bundle, c.ExperimentConfig(max_context_tokens=100000))
