"""Validate and materialize content-addressed editable bundles without executing them."""

import ast
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath

import yaml

from .contracts import ExperimentConfig, HarnessConfig, HarnessManifest, HarnessPatch, SubagentSpec

_PATTERN = re.compile(
    r"(?:memory/[\w.-]+\.md|tools/descriptions\.json|subagents/[\w-]+\.json|skills/[\w-]+/SKILL\.md|skills/[\w-]+/scripts/[\w.-]+\.py|scripts/[\w-]+\.py|middleware/[\w-]+\.py)\Z"
)


def safe_path(rel: str) -> str:
    p = PurePosixPath(rel)
    if not rel or p.is_absolute() or ".." in p.parts or "\\" in rel or str(p) != rel:
        raise ValueError(f"unsafe relative path: {rel}")
    return rel


def allowed_path(rel: str) -> bool:
    safe_path(rel)
    return rel in {"manifest.json", "system.md", "guardrails.md", "config.json"} or bool(
        _PATTERN.fullmatch(rel)
    )


def read_files(root: Path) -> dict[str, str]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("bundle root must be a real directory")
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symlink in bundle: {path.name}")
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            if not allowed_path(rel):
                raise ValueError(f"off-surface file: {rel}")
            out[rel] = path.read_text(encoding="utf-8")
    return out


def validate_bundle(root: Path, config: ExperimentConfig | None = None) -> HarnessManifest:
    files = read_files(root)
    try:
        manifest = HarnessManifest.model_validate_json(files["manifest.json"])
        tuning = HarnessConfig.model_validate_json(files["config.json"])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"invalid bundle manifest/config: {exc}") from exc
    config = config or ExperimentConfig()
    if (
        tuning.context_tokens > config.max_context_tokens
        or tuning.delegation_depth > config.max_delegation_depth
    ):
        raise ValueError("harness configuration exceeds experiment limits")
    if not files.get("system.md", "").strip():
        raise ValueError("system prompt is required")
    if len(files.get("system.md", "") + files.get("guardrails.md", "")) > 32000:
        raise ValueError("always-on prompt exceeds 32000 characters")
    for rel, body in files.items():
        if rel.endswith(".py"):
            try:
                ast.parse(body, filename=rel)
            except SyntaxError as exc:
                raise ValueError(f"invalid Python in {rel}: {exc.msg}") from exc
        if rel.startswith("subagents/"):
            SubagentSpec.model_validate_json(body)
        if rel == "tools/descriptions.json":
            data = json.loads(body)
            if not isinstance(data, dict) or not all(
                isinstance(k, str) and isinstance(v, str) and v for k, v in data.items()
            ):
                raise ValueError("tool descriptions must map names to nonempty strings")
        if rel.endswith("SKILL.md"):
            if not body.startswith("---\n") or "\n---" not in body[4:]:
                raise ValueError("skill frontmatter required")
            fm = yaml.safe_load(body.split("---", 2)[1])
            name = Path(rel).parent.name
            if not isinstance(fm, dict) or fm.get("name") != name or not fm.get("description"):
                raise ValueError("skill metadata must name its directory and description")
            if name not in files["system.md"] + files.get("guardrails.md", ""):
                raise ValueError("skill must be discoverable from always-on instructions")
        if rel.endswith(".md"):
            for ref in re.findall(r"(?<![\w/])((?:skills/[\w-]+/)?scripts/[\w.-]+\.py)", body):
                if ref not in files:
                    raise ValueError(f"missing script reference: {ref}")
    return manifest


def bundle_digest(root: Path) -> str:
    files = read_files(root)
    manifest = json.loads(files["manifest.json"])
    manifest.pop("digest", None)
    files["manifest.json"] = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def apply_patch(parent: Path, patch: HarnessPatch, destination: Path) -> str:
    validate_bundle(parent)
    if bundle_digest(parent) != patch.parent_digest:
        raise ValueError("patch parent mismatch")
    for rel in [*patch.upserts, *patch.deletions]:
        if not allowed_path(rel):
            raise ValueError(f"off-surface file: {rel}")
    if destination.exists():
        raise ValueError("destination must not already exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        stage = Path(tmp) / "candidate"
        shutil.copytree(parent, stage)
        for rel in patch.deletions:
            (stage / rel).unlink(missing_ok=True)
        for rel, body in patch.upserts.items():
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        validate_bundle(stage)
        digest = bundle_digest(stage)
        stage.rename(destination)
    return digest
