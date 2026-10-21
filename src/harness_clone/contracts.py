"""Versioned, strict records at component boundaries."""
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MODEL = "accounts/fireworks/models/deepseek-v4p1-flash"
COMPONENTS = ("system", "guardrails", "memory", "tools", "subagents", "skills", "scripts", "middleware")


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HarnessConfig(Record):
    context_tokens: int = Field(default=32768, ge=1024)
    compaction_fraction: float = Field(default=0.8, gt=0, lt=1)
    retrieval_limit: int = Field(default=5, ge=1, le=100)
    delegation_depth: int = Field(default=2, ge=0, le=8)


class ExperimentConfig(Record):
    model: Literal[MODEL] = MODEL
    mode: Literal["parallel", "sequential"] = "parallel"
    profile: Literal["research", "practical"] = "research"
    k: int = Field(default=1, ge=1, le=10)
    seed: int = 42
    max_usd: Decimal = Field(default=Decimal("0"), ge=0)
    stage_usd: Decimal = Field(default=Decimal("0.25"), gt=0)
    max_context_tokens: int = Field(default=131072, ge=1024)
    max_output_tokens: int = Field(default=4096, ge=64)
    max_tool_calls: int = Field(default=60, ge=1)
    max_delegation_depth: int = Field(default=3, ge=0, le=8)
    timeout_seconds: int = Field(default=180, ge=1, le=3600)
    max_memory_mb: int = Field(default=1024, ge=256)
    image: str = "harness-clone-executor:0.1"
    library_snapshot: str = "empty"
    harness_profile: str = "harness-clone-v1"
    editor_policy: Literal["text", "pi"] = "text"


class TaskSpec(Record):
    id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    source_root: Path
    output_paths: list[str] = Field(min_length=1)
    allow_empty: bool = False
    ordinal: int = Field(default=0, ge=0)


class HarnessManifest(Record):
    schema_version: Literal[1] = 1
    components: list[Literal["system", "guardrails", "memory", "tools", "subagents", "skills", "scripts", "middleware"]] = list(COMPONENTS)
    parent_digest: str | None = None
    digest: str | None = None
    provenance: dict[str, str] = Field(default_factory=dict)


class HarnessPatch(Record):
    parent_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    upserts: dict[str, str] = Field(default_factory=dict)
    deletions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def disjoint(self):
        if set(self.upserts) & set(self.deletions) or len(self.deletions) != len(set(self.deletions)):
            raise ValueError("conflicting or duplicate patch paths")
        return self


class Usage(Record):
    input_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    usd: Decimal = Field(default=Decimal("0"), ge=0)
    known: bool = True


class ExecutionRecord(Record):
    execution_id: str
    task_id: str
    bundle_digest: str
    status: Literal["completed", "failed", "unknown"] = "completed"
    delivered: bool = False
    artifacts: dict[str, str] = Field(default_factory=dict)
    trace: list[dict] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    error: str | None = None


class EvidenceSnapshot(Record):
    digest: str
    task_id: str
    records: list[ExecutionRecord] = Field(default_factory=list)
    library: list[dict] = Field(default_factory=list)


class EditProposal(Record):
    patch: HarnessPatch
    rationale: str
    evidence_refs: list[str] = Field(default_factory=list)
    keep_base: bool = False
    usage: Usage = Field(default_factory=Usage)


class SelectionRecord(Record):
    chosen_execution_id: str | None = None
    eligible: list[str] = Field(default_factory=list)
    reason: str = ""


class ExperienceEntry(Record):
    namespace: str
    domain: str
    task_id: str
    ordinal: int
    bundle_digest: str
    cue: str
    lesson: str
    evidence_refs: list[str] = Field(min_length=1)
    confidence: str = "observational"


class TaskResult(Record):
    task_id: str
    executions: dict[str, ExecutionRecord] = Field(default_factory=dict)
    stages: list[dict] = Field(default_factory=list)
    selection: SelectionRecord = Field(default_factory=SelectionRecord)
    errors: list[str] = Field(default_factory=list)


class SubagentSpec(Record):
    name: str = Field(pattern=r"^[a-z0-9_-]+$")
    description: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
