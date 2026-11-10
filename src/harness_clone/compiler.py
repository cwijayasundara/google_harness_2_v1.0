"""Compile a frozen bundle in a disposable execution worker.

This module loads executable plugins. Production callers must use DockerExecutor;
path validation is not a substitute for the container boundary.
"""

import importlib.util
import json
import shutil
import threading
import uuid
from pathlib import Path

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware import AgentMiddleware

from .bundles import validate_bundle
from .contracts import ExperimentConfig, HarnessConfig, SubagentSpec
from .providers import make_model


class ToolGuidance(AgentMiddleware):
    def __init__(self, descriptions):
        self.descriptions = descriptions

    def wrap_model_call(self, request, handler):
        tools = []
        for tool in request.tools:
            if hasattr(tool, "name") and tool.name in self.descriptions:
                tool = tool.model_copy(update={"description": self.descriptions[tool.name]})
            tools.append(tool)
        return handler(request.override(tools=tools))


class ToolCallLimit(AgentMiddleware):
    def __init__(self, limit):
        self.limit = limit
        self.calls = 0
        self.lock = threading.Lock()

    def wrap_tool_call(self, request, handler):
        with self.lock:
            if self.calls >= self.limit:
                raise RuntimeError("experiment tool-call limit reached")
            self.calls += 1
        return handler(request)


def select_memory_pages(
    pages: list[Path], tuning: HarnessConfig
) -> tuple[list[Path], list[Path]]:
    """Choose the injected memory layer within the token budget.

    ``memory/AGENTS.md`` (the index) always preloads first; remaining
    pages follow in sorted order while the cumulative size (chars/4 ≈
    tokens) fits ``memory_budget_tokens`` and the retrieval count cap.
    Returns (selected, skipped) — skipped pages stay readable on disk;
    the prompt names them so the agent knows to read them on demand.
    """
    ordered = sorted(pages, key=lambda p: (p.name != "AGENTS.md", p.name))
    budget = tuning.memory_budget_tokens * 4
    used = 0
    selected: list[Path] = []
    skipped: list[Path] = []
    for page in ordered:
        size = page.stat().st_size
        if page.name == "AGENTS.md":
            selected.append(page)  # the index is the top layer's core
            used += size
        elif len(selected) < tuning.retrieval_limit and used + size <= budget:
            selected.append(page)
            used += size
        else:
            skipped.append(page)
    return selected, skipped


def compile_agent(bundle_root: Path, backend, config: ExperimentConfig):
    manifest = validate_bundle(bundle_root, config)
    tuning = HarnessConfig.model_validate_json((bundle_root / "config.json").read_text())
    runtime = Path(backend.cwd)
    enabled = set(manifest.components)
    for component in ["memory", "skills", "scripts"]:
        if component in enabled and (bundle_root / component).exists():
            shutil.copytree(bundle_root / component, runtime / component, dirs_exist_ok=True)
    model = make_model(config, "executor")
    # A worker constructs one rollout; profile registration never mutates a live
    # controller or a concurrently running rollout in another worker.
    register_harness_profile(
        f"fireworks:{config.model}",
        HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)),
    )
    descriptions = (
        json.loads((bundle_root / "tools/descriptions.json").read_text())
        if "tools" in enabled and (bundle_root / "tools/descriptions.json").exists()
        else {}
    )
    summarizer = SummarizationMiddleware(
        model=make_model(config, "summarizer"),
        backend=backend,
        trigger=("tokens", int(tuning.context_tokens * tuning.compaction_fraction)),
        keep=("messages", 4),
    )
    common = [ToolGuidance(descriptions), summarizer, ToolCallLimit(config.max_tool_calls)]
    middleware = list(common)
    if "middleware" in enabled:
        for path in sorted((bundle_root / "middleware").glob("*.py")):
            spec = importlib.util.spec_from_file_location(f"hc_plugin_{uuid.uuid4().hex}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            factory = getattr(module, "create_middleware", None)
            if not callable(factory):
                raise ValueError("middleware must export create_middleware(config)")
            instance = factory(tuning.model_dump())
            if not isinstance(instance, AgentMiddleware):
                raise ValueError("plugin must produce AgentMiddleware")
            middleware.append(instance)
    subagents = []
    if "subagents" in enabled and tuning.delegation_depth > 0:
        for path in sorted((bundle_root / "subagents").glob("*.json")):
            sub = SubagentSpec.model_validate_json(path.read_text())
            subagents.append(
                {
                    **sub.model_dump(),
                    "model": make_model(config, "subagent"),
                    "middleware": list(common),
                    "skills": [str(runtime / "skills")] if "skills" in enabled else None,
                }
            )
    prompt = (bundle_root / "system.md").read_text()
    if "guardrails" in enabled and (bundle_root / "guardrails.md").exists():
        prompt += "\n\n" + (bundle_root / "guardrails.md").read_text()
    prompt += f"\nRetrieve at most {tuning.retrieval_limit} relevant memory/skill entries per lookup. Delegate at most {tuning.delegation_depth} levels; do not create additional execution agents."
    memory = None
    if "memory" in enabled:
        selected, skipped = select_memory_pages(
            # memory/instructions.md steers the reflect step, not task
            # agents; it stays editable in the bundle but is not preloaded.
            [
                p
                for p in sorted((runtime / "memory").glob("*.md"))
                if p.name != "instructions.md"
            ],
            tuning,
        )
        memory = [str(p) for p in selected] or None
        if skipped:
            names = ", ".join(p.name for p in skipped)
            prompt += (
                f"\nMemory pages not preloaded within the token budget "
                f"(read on demand): {names}."
            )
    return create_deep_agent(
        model=model,
        system_prompt=prompt,
        backend=backend,
        middleware=middleware,
        subagents=subagents,
        skills=[str(runtime / "skills")] if "skills" in enabled else None,
        memory=memory,
    )
