# Harness² Deep Agents MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for inline execution, or superpowers:subagent-driven-development if the user selects delegation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local CLI that optimizes all eight harness components through reproducible Propose–Probe–Compose experiments, with Fireworks DeepSeek and Pi Durable code editing.

**Architecture:** A Python LangGraph controller invokes isolated Deep Agents rollouts, persists immutable bundles and evidence, and appends ordered domain experience. A separate TypeScript Pi Durable worker edits code-bearing bundles. Offline evaluation has no write path into online refinement evidence.

**Tech Stack:** Python 3.12 managed by uv; Deep Agents, LangGraph, langchain-fireworks, Pydantic, SQLite, pytest, Ruff; Node >=22.19.0, TypeScript, Pi Durable, Vitest; Docker Linux workers.

**Spec:** [Approved proposal](../../harness2-deepagents-proposal.md), supplemented by the user's requirement that memory, skills, system prompts, middleware, scripts, and all other harness components be optimizable.

## Global Constraints

- Use `accounts/fireworks/models/deepseek-v4p1-flash` for all agent roles initially.
- Pin published package versions and all transitive dependencies during implementation.
- Use Linux containers for rollouts and editing jobs, including when developing on macOS.
- Use a fresh compiled executor, thread ID, sandbox, and memory overlay for every rollout.
- Record KEEP_BASE separately from validation or transport failure.
- Keep benchmark answers, private tests, grader prompts, and scores outside every agent mount and network route.
- Keep controller, evaluator, credential access, and operational spending/isolation limits outside the editable harness surface.
- Research mode executes k references, 2k candidate probes, and k composed rollouts; selection is among composed outputs only.
- Paid model calls require configured credentials and an explicit nonzero user-supplied spending cap. Default cap is zero; never print credentials.
- Mutable working memory is private to a rollout. Publishing memory or experience changes requires adoption.

## Scope decisions

Implement one complete vertical slice before adding upstream benchmark integrations. The initial adapter consumes local JSON task specifications with source files and deliverable contracts, including small code tasks. A separately invoked evaluator uses public fixture assertions or user-supplied grading commands. This provides useful software without downloading benchmark datasets.

Implement parallel and sequential modes at k=1 and k=2, all eight editable categories, tunable harness configuration, ordered experience, and Pi code editing. Default to research parity. Practical mode initially changes only the selection pool to allow baseline retention; early stopping and automatic global promotion are deferred. Full JobBench/LAB/WorkBuddy adapters and the statistical benchmark study are follow-on plans.

Provider checks have an offline contract portion and a live portion. Build and test the offline system even if credentials or a paid-call cap are unavailable. Mark live compatibility as unverified until the real checks pass; a fixture run must never be reported as provider validation.

The workspace currently contains only the proposal, has no Git repository, and has uv, Python 3.14, Node 24.1.0, npm, Docker CLI, and Git available. Python 3.12 availability, Docker daemon operation, network access, and published dependency compatibility remain to be checked at execution time. Initialize Git at execution, preserve the proposal, and create a feature branch. Use an in-workspace worktree if supported by local permissions; otherwise use the new feature branch in this new repository and record that choice.

## Review Focus

1. Interrupted publication or duplicate RPC delivery must produce one committed candidate/job, covered by Tasks 2 and 8.
2. Paths, symlinks, and shell execution must not escape the candidate workspace, covered by Tasks 1 and 4.
3. Missing artifacts or transport errors must remain distinct from valid unchanged proposals and valid empty deliverables, covered by Tasks 4 and 6.
4. An unknown model profile or compaction path must never trigger an implicit alternative provider, covered by Tasks 3 and 5.
5. Resume, concurrent domains, and changed experiment settings must not leak future experience or reuse incompatible results, covered by Tasks 2 and 7.

## File map

```text
pyproject.toml, uv.lock                 Python package and exact environment
src/harness_clone/contracts.py         Versioned domain records and protocols
src/harness_clone/bundles.py           Editable surface, hashing, patches, validation
src/harness_clone/storage.py           SQLite metadata and immutable artifact publication
src/harness_clone/providers.py         Explicit Fireworks model creation and accounting
src/harness_clone/preflight.py         Offline/live capability checks
src/harness_clone/sandbox.py           Docker lifecycle and worker command interface
src/harness_clone/worker.py            Isolated Deep Agents execution entrypoint
src/harness_clone/compiler.py          Bundle-to-Deep-Agents construction
src/harness_clone/evidence.py          Whitelisted evidence assembly
src/harness_clone/editors.py           INV/DOM/COMPOSE/SELECT/REFLECT roles
src/harness_clone/controller.py        LangGraph stages and scheduling
src/harness_clone/experience.py        Ordered, versioned domain library
src/harness_clone/pi_client.py         Durable editing RPC client
src/harness_clone/tasks.py             Local task adapter
src/harness_clone/evaluate.py          Offline evaluation entrypoint
src/harness_clone/cli.py               doctor, demo, run, resume, inspect, evaluate
workers/pi/src/                        TypeScript Durable host and job API
containers/                           Execution/editor images and launch configuration
examples/base-harness/                 All component slots and initial configuration
examples/tasks/                        Local demonstrator tasks
tests/                                Contract, integration, isolation, and recovery checks
README.md, .env.example, .gitignore    Installation and operating instructions
```

Each source file owns the responsibility stated above. Split a module when it gains unrelated responsibilities; avoid creating a generic utility layer.

## Task 1: Define the editable harness and task contracts

**Files:** Create `pyproject.toml`, `src/harness_clone/__init__.py`, `contracts.py`, `bundles.py`, `tests/test_bundles.py`, and `examples/base-harness/`.

**Interfaces:** Produce Pydantic records `ExperimentConfig`, `TaskSpec`, `HarnessManifest`, `HarnessPatch`, `Usage`, `ExecutionRecord`, `EvidenceSnapshot`, `EditProposal`, `SelectionRecord`, and `ExperienceEntry`. `TaskSpec` includes ID, domain, prompt, source root, explicit output paths, and sequence ordinal. `BundleStore` later refers to bundles by SHA-256 digest. Export `validate_bundle(root: Path) -> HarnessManifest`, `bundle_digest(root: Path) -> str`, and `apply_patch(parent: Path, patch: HarnessPatch, destination: Path) -> str`.

- [ ] Write `test_bundle_covers_all_component_categories`: a valid example has system, guardrails, memory, tool guidance, subagents, skills, scripts, and middleware/plugin slots; each category can be changed or explicitly disabled without editing controller files.
- [ ] Write parameterized tests for `../`, absolute paths, symlink escape, invalid skill metadata, missing script references, invalid subagent schema, and off-surface files. Assert rejection before code imports or writes outside destination.
- [ ] Run `uv run pytest tests/test_bundles.py -q`; confirm failure from missing behavior.
- [ ] Implement the records and validator. Add `config.json` for editable context token limit, compaction trigger fraction, retrieval item limit, and delegation depth. Validate positive limits, fraction strictly between zero and one, and user-configured hard ceilings from ExperimentConfig. No arbitrary configuration keys.
- [ ] Define patches as explicit upserts and deletions against `parent_digest`; reject mismatched parents and duplicate/conflicting paths. Use canonical JSON and sorted relative UTF-8 file paths/content for hashes. Exclude manifest's self-digest field from its digest input.
- [ ] Run the bundle tests; confirm all cases pass. Commit the independently testable bundle contract and example.

## Task 2: Persist artifacts, experiment identity, and budget reservations

**Files:** Create `storage.py`, `tests/test_storage.py`, `tests/test_budget.py`.

**Interfaces:** Produce `RunStore(root: Path)`, with `create_experiment(config: ExperimentConfig) -> str`, `publish_bundle(root: Path) -> str`, `save_execution(record: ExecutionRecord) -> None`, and `completed_execution(execution_id: str) -> ExecutionRecord | None`. Produce `BudgetLedger.reserve(stage_id: str, ceiling_usd: Decimal) -> None` and `settle(stage_id: str, usage: Usage) -> None`. Store immutable artifacts under digest paths and stage records in SQLite.

- [ ] Write `test_resume_rejects_changed_model_or_library_snapshot`, `test_identical_bundle_different_draws_are_not_deduplicated`, and `test_partial_publication_never_appears_complete` with injected failure before atomic rename/metadata commit.
- [ ] Write `test_parallel_reservations_cannot_exceed_cap` and `test_settlement_is_idempotent`; include zero-cap rejection and missing-usage status without invented billing.
- [ ] Run `uv run pytest tests/test_storage.py tests/test_budget.py -q`; confirm the tests fail.
- [ ] Implement transactional stage records, explicit running/completed/failed/unknown states, content verification on resume, atomic artifact publication, and Decimal currency accounting. Cache identity includes model/options, task hash, image digest, profile, library snapshot, and draw identity.
- [ ] Run both test files and commit after passing. Record actual usage even if a provider exceeds an estimate; stop scheduling further paid work at the cap and report the overrun.

## Task 3: Establish the exact Fireworks provider contract

**Files:** Create `providers.py`, `preflight.py`, `tests/test_providers.py`, `.env.example`, and dependency lockfiles.

**Interfaces:** Produce `make_model(config: ExperimentConfig, role: str) -> BaseChatModel`, `normalize_usage(response: AIMessage) -> Usage`, and `run_preflight(config: ExperimentConfig, live: bool) -> PreflightReport`. The report distinguishes unavailable, offline-pass, live-pass, and failed capabilities.

- [ ] Write tests asserting the exact model path is supplied for executor, INV, DOM, COMPOSE, SELECT, REFLECT, subagent, and summarizer roles. Assert an unknown profile never constructs an Anthropic/OpenAI-default model.
- [ ] Write fixtures for cached tokens, absent usage, reasoning plus tool calls, streaming tool argument assembly, and retryable versus permanent provider errors. Test that live preflight rejects a zero cap before any request.
- [ ] Run `uv run pytest tests/test_providers.py -q`; confirm failure.
- [ ] Resolve published compatible Deep Agents, LangGraph, langchain-fireworks, and checkpoint packages into `uv.lock`. Use the proposal's versions as research references; do not silently replace APIs when a referenced version is unavailable. Implement explicit native ChatFireworks construction with per-role options and bounded retries.
- [ ] Implement opt-in live checks for a tool round trip, structured proposal, stream, usage, reasoning history, and compaction continuation. Treat unsupported reasoning settings as failed capabilities, not a reason to switch models.
- [ ] Run offline tests, inspect dependency resolution, and commit. Run live checks only when the configured cap and credentials permit, saving the capability report.

## Task 4: Execute tasks in isolated Docker workers

**Files:** Create `sandbox.py`, `worker.py`, `tasks.py`, `containers/executor.Dockerfile`, `tests/test_sandbox.py`, and `tests/integration/test_isolation.py`.

**Interfaces:** Produce `execute_rollout(task: TaskSpec, bundle_digest: str, execution_id: str, config: ExperimentConfig) -> ExecutionRecord`. Worker consumes a versioned JSON request and emits a terminal JSON record plus artifacts. Docker lifecycle is owned by Python; the Deep Agents sandbox backend supplies file and execute tools inside that worker.

- [ ] Write tests that two executions cannot see or modify each other's scratch, memory, or outputs; source and published bundle mounts are read-only; symlinks cannot make artifact collection read host files.
- [ ] Write timeout, nonzero exit, missing output, explicitly permitted empty output, and killed-worker cases. Assert each gets its own delivery/error status and partial outputs never imply success.
- [ ] Run `uv run pytest tests/test_sandbox.py -q`; confirm failure before implementation.
- [ ] Implement separate read-only source/bundle mounts and writable scratch/output mounts, non-root execution, resource limits, no Docker socket, and process cleanup. Use argv arrays for host commands and explicit task output contracts for artifact collection.
- [ ] Route inference through a host-controlled gateway that injects credentials; do not mount a Fireworks key into candidate containers. Limit worker networking to that gateway, and reject a configuration that claims isolation without enforcement. The gateway accepts only the configured model and charges the experiment ledger. Browser/network tasks are outside this first adapter.
- [ ] Run unit tests plus `uv run pytest tests/integration/test_isolation.py -q` against the built image; verify Docker daemon availability explicitly. Commit after passing; report unavailable Docker checks as unrun.

## Task 5: Compile every harness component into Deep Agents

**Files:** Create `compiler.py`, `tests/test_compiler.py`, `tests/integration/test_executor.py`; extend worker and example bundle.

**Interfaces:** Produce `compile_agent(bundle_root: Path, backend: BackendProtocol, config: ExperimentConfig) -> CompiledStateGraph`. Compilation occurs only inside the execution worker. The backend namespace exposes private runtime memory overlays; immutable adopted memory remains the seed.

- [ ] Write parameterized tests for all eight components plus config. Assert each changes the constructed runtime behavior: prompt content, guardrail middleware, memory load, tool description, subagent specialization, skill discovery, script invocation, and middleware event handling.
- [ ] Write `test_tool_guidance_cannot_change_tool_schema`, `test_every_subagent_uses_explicit_model`, and `test_compaction_continues_with_same_provider`.
- [ ] Run `uv run pytest tests/test_compiler.py -q`; confirm failure.
- [ ] Implement pinned profile assembly with fixed middleware ordering and explicit model routing. Use public supported overrides or wrappers; never monkey-patch shared process globals. Validate executable entrypoints in the container and compile a new graph for each run.
- [ ] Run unit tests and one container integration task that reads a skill, runs a script, and produces a contracted artifact. Confirm its private memory edits do not alter the stored bundle. Commit after passing.

## Task 6: Implement Propose–Probe–Compose and adoption

**Files:** Create `evidence.py`, `editors.py`, `controller.py`, `tests/test_controller.py`, and `tests/test_evidence.py`.

**Interfaces:** Define injectable `Executor.execute(...) -> ExecutionRecord` and `Editor.propose(scope: str, parent_digest: str, evidence: EvidenceSnapshot) -> EditProposal`. Produce `run_task(task: TaskSpec, config: ExperimentConfig, store: RunStore, executor: Executor, editor: Editor) -> TaskResult`. `TaskResult` records executions, stage decisions, chosen output, and errors.

- [ ] Write deterministic-editor tests asserting k=1 gives 4 executions/3 editing stages and k=2 gives 8/6 in each mode. At k=2 sequential, assert round-two parents derive from checkpoint one; parallel candidates share the original parent.
- [ ] Write tests that research selection excludes baseline/raw proposals, practical selection can retain baseline, and k=1 research adoption needs no selector model call. An invalid proposal gets one repair; failed repair falls back with a failure record distinct from KEEP_BASE.
- [ ] Write evidence tests asserting only allowlisted task files, artifacts, and traces enter context. Put grader-like filenames and score records in sibling storage and assert they are never copied. Validate every cited trace/artifact reference before accepting an edit rationale.
- [ ] Run `uv run pytest tests/test_controller.py tests/test_evidence.py -q`; confirm failure.
- [ ] Implement explicit LangGraph nodes with persisted stable stage IDs, phase budget reservations, original references, independent INV/DOM contexts, bounded repair, selective composition, anonymized output choice, and terminal failure when no eligible deliverable exists. Do not merge artifact contents while selecting an existing output.
- [ ] Run deterministic tests and a credential-free k=2 demo. Verify exact graph counts and ancestry from the stored records. Commit after passing.

## Task 7: Add cross-task experience and causal resume

**Files:** Create `experience.py`, `tests/test_experience.py`, `tests/integration/test_resume.py`; extend controller/storage.

**Interfaces:** Produce `ExperienceLibrary.snapshot(namespace: str, domain: str, before_ordinal: int) -> EvidenceSnapshot` and `append(entry: ExperienceEntry) -> str`. Entry includes adopted full bundle digest, task identity, ordinal, cue, lesson, supporting references, and confidence notes. Produce `resume_experiment(experiment_id: str, store: RunStore) -> ExperimentResult`.

- [ ] Write tests that task B sees task A's complete adopted bundle and lesson, never B/future entries; unrelated domains and model/mode/k/seed namespaces remain separate. A task with insufficient reflection evidence adds no shared entry.
- [ ] Write recovery tests interrupting after probes, after selection, and during library publication. Assert completed stages are not rerun, append is idempotent, and resumed results preserve the original library snapshot.
- [ ] Run `uv run pytest tests/test_experience.py tests/integration/test_resume.py -q`; confirm failure.
- [ ] Implement ordered per-domain task streams, immutable snapshots, full-bundle retrieval index, and controller-only append. Permit domain concurrency with explicit namespace locks; freeze snapshots for holdout mode.
- [ ] Run the tests and a two-task same-domain demo, verifying the second task still starts from the configured base while earlier experience informs its editors. Commit after passing.

## Task 8: Integrate the Pi Durable editing worker

**Files:** Create `workers/pi/package.json`, `package-lock.json`, `tsconfig.json`, `src/server.ts`, `src/jobs.ts`, `src/models.ts`, `src/edit.ts`, `test/jobs.test.ts`, `containers/pi.Dockerfile`, `pi_client.py`, and `tests/test_pi_client.py`.

**Interfaces:** Define versioned `EditJob` JSON shared with Python: request ID, parent/evidence digests, scope, allowed paths, resource limits, and routing-policy version. Expose `POST /jobs`, `GET /jobs/:id`, `GET /jobs/:id/result`, and `POST /jobs/:id/cancel`. Return persisted job status, usage, candidate digest, structured diff, rationale, and evidence references.

- [ ] Write worker tests that duplicate request IDs return one job/submission, a repeated ID with changed payload is rejected, cancellation reaches the owned task, and reopening SQLite reacquires an interrupted submission. Write Python client tests for lost admission responses and duplicate polling.
- [ ] Run `npm test --prefix workers/pi` and `uv run pytest tests/test_pi_client.py -q`; confirm missing behavior fails.
- [ ] Pin compatible Pi packages; install CodingTools and a container execution environment. Register the exact Fireworks model explicitly when the published catalog lacks it. Confirm the model definition/transport with the provider contract; never infer capabilities from a model name alone.
- [ ] Implement admission and result publication with durable request identity. Restrict writes to candidate edits, use the same credential gateway and OS boundaries as executor workers, and reconcile ambiguous shell actions in disposable directories. Return candidate artifacts for Python validation; Pi cannot publish adoption or modify controller/evaluator code.
- [ ] Route scripts and middleware edits through Pi under a fixed recorded policy. Text edits remain supported through Deep Agents; composition can call Pi for code-bearing outcomes without granting controller access.
- [ ] Run worker/client tests, an offline fake-provider process-restart integration, and a live bounded edit only when authorized by cap/configuration. Commit and report offline/live status separately.

## Task 9: Ship CLI, offline evaluation, and end-to-end verification

**Files:** Create `cli.py`, `evaluate.py`, `tests/test_cli.py`, `tests/integration/test_mvp.py`, `README.md`, `.gitignore`, and `examples/tasks/`.

**Interfaces:** Export console command `harness-clone` with `doctor`, `demo`, `run`, `resume`, `inspect`, and `evaluate`. `run` accepts task path, base bundle, mode, k, profile, run directory, and explicit spending cap. `demo` uses deterministic providers without credentials; `evaluate` operates on immutable completed outputs and writes separate offline results.

- [ ] Write CLI tests for invalid configs, zero-cap live runs, missing Docker, incompatible resume, readable failure output, and task source/output paths containing spaces. Assert `demo` performs no network calls.
- [ ] Write an end-to-end acceptance case: k=1 and k=2 schedules, all component mutation types, two ordered same-domain tasks, one Pi code edit, interruption/recovery, and an offline evaluation record inaccessible to agent evidence.
- [ ] Run `uv run pytest tests/test_cli.py tests/integration/test_mvp.py -q`; confirm new acceptance behavior fails before wiring CLI commands.
- [ ] Implement CLI and separate evaluator. Document install, exact model/configuration, component authoring, snapshot provenance, practical/research differences, gateway/container assumptions, and the distinction between deterministic fixtures and live verified compatibility. Add a spec-coverage table for every required component and runtime behavior.
- [ ] Run `uv run ruff check .`, `uv run pytest -q`, `npm test --prefix workers/pi`, `npm run build --prefix workers/pi`, the Docker integration suite, and `uv run harness-clone demo --mode parallel --k 2` plus sequential mode. Record unavailable integration checks honestly; do not claim a usable live system without successful provider and isolation checks.
- [ ] Review the whole change for requirement coverage and failure behavior, commit the verified result, and provide usage commands plus remaining live prerequisites. Do not publish or push without existing session authorization.

## Follow-on work

After this vertical slice is verified, write separate plans for one chosen upstream benchmark adapter and the matched-cost ablation study. A dashboard, distributed workers, adaptive early stopping, learned retrieval policies beyond the declared configuration, and deeper orchestration mutations remain separate increments. The current plan supports optimizing memory contents/skills/prompts/code and declared retrieval/compaction settings; changing the implementation of memory retrieval or orchestration itself requires a validated middleware/plugin entrypoint or a later extension of the editable surface.

## Self-review result

The plan covers the proposal's execution, recursion, experience, provider, Pi, isolation, persistence, and accounting requirements. It explicitly phases out full upstream benchmark replication and broad performance claims. Each task has named contracts and red/green verification; the five review risks are assigned to owning tests. The approved architectural proposal remains the source specification; this plan supplies concrete local-workload and research-default choices for user review before implementation.
