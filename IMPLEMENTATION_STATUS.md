# Harness² clone — handoff to Muse

Updated: 21 October 2026. Implementation stopped at the user's request to continue from Muse.

## Portfolio agent update (Tasks 2–9, 21 October 2026)

The self-evolving portfolio agent is implemented on `main` and proven offline: eToro client with CSV fallback, market providers/screener, frozen snapshot tasks, portfolio bundle with recommendation validator, offline scorer, `portfolio fetch|daily|evolve|score` CLI, and point-in-time historical bootstrap. Task 9 adds `tests/test_portfolio_integration.py` (fake executor/editor through `run_daily` plus one sequential `run_evolution` over three dated tasks; reflected lessons only flow forward) and a `docker`-marked in-container `portfolio_math.py` test in `tests/test_sandbox.py`. Current verification: Python suite 191 passed, 3 skipped; Ruff clean; Pi worker 3 passed and TypeScript build passed. The new Docker test is written and marked correctly but **unverified in this environment** (Docker socket access denied; with `HARNESS_TEST_DOCKER=1` all three docker-marked tests fail on daemon access, and without it the test skips).

Still deferred/unverified, unchanged by Task 9: no paid calls were made, so live portfolio runs (`portfolio daily`/`evolve`) and Task 1's complete live experiment run remain unverified; the eToro live payload shape is fixture-based until confirmed with a real key; `uv.lock` still needs `uv lock && uv sync` outside the sandbox before live equity fetching (`yfinance` is declared and lazily imported). After live verification, operation is daily `portfolio daily`, weekly `portfolio evolve` + `portfolio score`, and the 4-week forward soak as an operational phase before reports inform any manual decision. The sections below predate the portfolio work and describe the earlier harness-clone handoff.

## Start here

**The implementation is in an isolated worktree, not the top-level checkout:**

```text
/Users/chamindawijayasundara/Documents/learning_102/google_harness_2_v1.0/.worktrees/implementation
```

```bash
cd /Users/chamindawijayasundara/Documents/learning_102/google_harness_2_v1.0/.worktrees/implementation
git status --short
```

- Implementation branch: `feat/implementation`.
- Committed implementation HEAD: `17c9021dac7497a63ca3fac1e045da56f6aad3a9`.
- Implementation base: `8118cc3`.
- Top-level checkout is on `feat/harness-clone` at `8118cc3`; it contains the approved documents and workspace setup, **not the implemented source tree**. Do not restart implementation there or reset either checkout.
- The user approved the written implementation plan and inline execution. No renewed design approval is needed to finish the approved scope.

**Overall status:** the local research MVP is implemented and its offline workflow works. The seven final-review defects below were fixed in an uncommitted fix pass on 21 October 2026, each with a failing-first regression in `tests/test_final_review.py` (16 tests). Full Python suite: 99 passed, 2 skipped; Docker suite: 2 passed; Node/Vitest: 3 passed; TypeScript build and Ruff pass. Do not call it live-verified yet: no paid model calls have been performed, and no spending cap has been approved.

## User requirements and approved scope

Reproduce Harness²'s task-level Propose–Probe–Compose loop and cross-task experience learning using Python Deep Agents, with `accounts/fireworks/models/deepseek-v4p1-flash` on Fireworks for all model roles. Use Pi Durable for executable harness editing.

All eight component categories must be editable: system prompts, guardrails, memory, tool guidance, subagents, skills, scripts, and middleware/plugins. Declared context/compaction/retrieval/delegation settings are also editable. Controller, evaluator, account credentials, isolation rules, and hard spending limits remain outside the editable surface.

Source documents, relative to either checkout:

- `docs/harness2-deepagents-proposal.md` — approved architecture and research.
- `docs/superpowers/plans/2026-10-21-harness2-deepagents.md` — approved nine-task implementation plan.
- Worktree `README.md` — commands and current MVP limitations. Its Docker-unverified wording predates the successful Docker checks below; update it after the remaining fixes.

## What exists

Python package `src/harness_clone/`:

- Strict records, editable-surface validation, patching, SHA-256 bundle versions.
- Transactional SQLite run records, atomic bundle publication, request-level spending reservations, per-stage usage aggregation.
- Explicit Fireworks model construction and offline/opt-in live preflight.
- Docker execution workers and fixed-upstream inference gateway. Candidate containers receive a scoped gateway token; the account key remains in the trusted gateway.
- Compiler for real Deep Agents graphs: prompts, guardrails, memory, tools, named subagents, skills, scripts, middleware; configurable compaction and bounded startup memory selection; shared tool-call limit.
- Explicit LangGraph recursion controller for parallel and sequential search, research/practical adoption, bounded proposal repair, and completed-stage reuse.
- Per-domain, ordinal-scoped experience snapshots retaining full adopted bundle references and evidence-supported lessons.
- Filesystem-only Deep Agents text editors; isolated Pi editing bridge with persistent job directories.
- CLI: `doctor`, `demo`, `run`, `resume`, `inspect`, `evaluate`.
- Separate offline exact-text/JSON artifact evaluator.

TypeScript worker `workers/pi/`:

- Pi Durable/Chord/pi-ai pinned to `1.3.0`.
- SQLite job identity, HTTP submit/status/result/cancel API, CodingTools, explicit Fireworks model via gateway, Durable session persistence and candidate export.

Other deliverables:

- `examples/base-harness/` contains all eight component slots and configuration.
- `examples/tasks/summary/` contains a local numeric-summary task.
- `containers/executor.Dockerfile`, `containers/pi.Dockerfile`.
- `containers/compose.yaml` builds both images; this file is currently **untracked**.
- Wheel and source distribution built successfully under ignored `dist/`.

## Verification actually performed

- Last full Python suite before adding the second Docker integration test: **83 passed, 1 skipped**.
- After adding the second integration test, explicitly enabled Docker suite: **2 passed, 83 deselected** in 9.47 seconds. A full suite has NOT been rerun since that addition.
- Docker tests verify read-only/non-root/no-public-egress behavior and a real Deep Agents graph executing a candidate script in a container with private memory updates that do not change the seed bundle.
- Node/Vitest: **3 passed**, including real Pi Durable SQLite reopen and duplicate-submission identity behavior.
- TypeScript build passed.
- Ruff check passed before the latest integration-test addition; run formatting/lint again because that addition is not formatted yet.
- Both deterministic CLI demos ran successfully at `k=2`, each processing two tasks with **16 executions and 12 editing stages**, no errors.
- `uv build` produced a wheel and sdist successfully.
- Both Docker images built successfully through Compose:
  - `harness-clone-executor:0.1`
  - `harness-clone-pi:0.1`
- Docker Desktop was started during this session and is currently running; daemon reported `29.7.2`.
- `doctor` reported Deep Agents `0.7.29`, LangGraph `1.2.16`, langchain-fireworks `1.7.1`, Docker available, and a Fireworks key present.

**No paid model calls have been performed.** Credential presence was checked without printing its value. Live Fireworks tool/reasoning/streaming compatibility, complete Pi editing over the real provider, and controller-death/container recovery remain unverified.

A budget question was sent just before the user chose to hand off. **No spending cap has been approved. Keep paid calls disabled until the user supplies one.** Do not interpret the UI's preselected option as approval.

## Fresh final review — seven important defects, fixed 2026-10-21 (uncommitted)

The fresh reviewer inspected committed range `8118cc3..17c9021` read-only, reported no critical findings, and assessed **ready with fixes**. The fix pass is complete in the working tree: (1) CLI admits stream identity via `experience.check_stream` before overwriting runtime metadata; (2) `BudgetLedger.allocate/release` phase allocations plus cumulative per-stage ceilings in the gateway (repair attempts share a stage group; controller pre-allocates every planned phase); (3) Pi editor persists session credentials and reconciles existing network/containers, converting Docker failures to `RuntimeError`; (4) `BudgetLedger.mark_unknown` converts a definitively stopped gateway's unsettled requests to unknown at editor/sandbox/Pi shutdown; (5) evaluation rejects outputs inside any persisted task source directory; (6) proposal/compose contexts are frozen snapshots, so INV/DOM siblings are independent while COMPOSE and later sequential rounds see earlier stages; (7) executor/Pi image IDs are resolved at admission, persisted in `ExperimentConfig`, and re-verified on resume. The findings below are the original report, kept for reference.

1. **Rejected live runs poison resume metadata.** `src/harness_clone/cli.py:124–127` overwrites `runtime/tasks` before `run_stream` rejects incompatible task/source inputs. Original run succeeds; attempted changed task correctly fails, but then `resume` also fails because its persisted tasks have been replaced. Admit stream identity and runtime metadata transactionally; add a regression proving a rejected invocation leaves the original run resumable.

2. **Stage ceilings and future composition budget are not enforced.** `gateway.py:72–77`, `controller.py:69–101`: request-level reservations use the experiment cap only. `stage_usd` is transported in Pi jobs but not enforced cumulatively, and no budget is reserved for later composition/final execution. The reviewer reproduced a request accepted above an extremely small stage cap. Implement transactional phase allocations and cumulative stage ceilings, including repairs/delegation, without double-counting allocations and their requests.

3. **First Pi resume after controller death cannot reconnect to existing resources.** `pi_editor.py:91–102`, `184–187`: deterministic Docker network/container names are created unconditionally. A killed controller leaves them present, so resume fails on network creation before reacquiring the persisted job; cleanup then removes the old containers. `CalledProcessError` also escapes bounded repair handling. Reconcile request-owned resources on first resume; persist scoped session credentials/admission details or deliberately recreate containers while preserving Durable storage. Test one-resume, one-submission recovery.

4. **Abandoned provider calls are not marked unknown.** `storage.py:150–158`, gateway settlement and worker cleanup: gateway death after upstream admission leaves `usage=NULL`. Later reservations treat it as a pending estimate rather than unknown billing, allowing more paid work. Track owner/liveness and convert requests to unknown when their gateway is definitively stopped, while preserving legitimate concurrent in-flight calls. Test death between admission and settlement.

5. **Offline evaluation output can contaminate task sources.** `evaluate.py:45–50` rejects outputs beneath `store.root` only. Persisted task source directories usually live elsewhere. Writing evaluation to `source_root/metrics.json` succeeds, and `read_sources` subsequently includes the scores. Resolve and reject evaluation destinations within every persisted task source directory; test an innocuously named score file.

6. **INV/DOM contexts are not independent.** `controller.py:99–100`, `editors.py`, `pi_editor.py`: each proposal receives cumulative `result.stages`. At k=1, DOM sees INV's rationale and candidate digest; later parallel siblings see earlier sibling decisions. Freeze proposal-phase context before generating siblings. Supply their decisions to COMPOSE; sequential rounds may retain causally earlier round evidence.

7. **Cache identity lacks immutable runtime image identities.** `contracts.py:47`, `controller.py:49–55`, `pi_editor.py:163`: configuration contains the executor's mutable image tag; the Pi image tag is hardcoded outside configuration. Rebuilding a tag leaves experiment identity unchanged and permits incompatible cached reuse. Resolve and persist executor and Pi image IDs/digests before live experiment admission; reject changed images on resume.

Suggested next action: write focused failing regressions for these findings, fix them, run the full suites and real Docker tests, then update status/docs. The inline workflow calls for one fix pass verified with tests; do not silently mark findings resolved without reproductions.

Review items explicitly set aside: unauthorized paid-provider verification, upstream benchmark scores/adapters, simultaneous dispatch of parallel search, recursive delegation beyond one level, learned retrieval policies, binary artifact rendering/richer graders, and distributed controllers. These remain declared limitations/follow-on work, not hidden successful verification.

## Current uncommitted work — preserve it

At handoff, implementation worktree Git status before writing this status copy:

```text
 M tests/integration/test_isolation.py
?? containers/compose.yaml
```

The test modification adds the **passing real container Deep Agents/script/private-memory integration test**. Compose builds both images successfully. Neither change has been committed. Writing this handoff adds `IMPLEMENTATION_STATUS.md` to both checkouts as another untracked file. No branch has been merged or pushed.

The top-level and worktree copies of this handoff intentionally contain the same text so Muse can find the implementation from either location.

## Resume commands

From the implementation worktree:

```bash
uv sync --cache-dir /tmp/harness-clone-uv --frozen
npm ci --prefix workers/pi --cache /tmp/harness-clone-npm
uv run --cache-dir /tmp/harness-clone-uv ruff format .
uv run --cache-dir /tmp/harness-clone-uv ruff check .
uv run --cache-dir /tmp/harness-clone-uv pytest -q
npm test --prefix workers/pi
npm run build --prefix workers/pi
docker compose -f containers/compose.yaml --profile build build
```

The exact command successfully used for the real Docker tests was:

```bash
.venv/bin/python -c 'import os,pytest; os.environ["HARNESS_TEST_DOCKER"]="1"; raise SystemExit(pytest.main(["-m","docker","-q","--tb=short"]))'
```

The equivalent ordinary shell command is `HARNESS_TEST_DOCKER=1 uv run pytest -m docker -q`. In this Codex environment, direct `docker build` was denied socket access while the permitted Compose invocation worked. Do not weaken isolation to work around a denied command.

Demo artifacts already exist outside the repository at `/tmp/harness-demo-parallel` and `/tmp/harness-demo-sequential`. Use new directories for experiments with changed configuration/source/code identity.

## Progress records and decisions

Do not restart from Task 1. Commits already implement all nine areas:

```text
1c1f1c7 bundle validation/contracts
6cf6d05 immutable run storage and budget reservations
2d09e25 explicit Fireworks routing/preflight
7b8881b container worker and inference gateway
5092e61 Deep Agents compiler
01abfc6 recursion controller and text editors
21f46b2 causal cross-task experience
3a5574e Pi Durable integration
17c9021 CLI, demos, offline evaluation, runtime contracts and formatting
```

Detailed plan-local ledger, briefs, and review diff are in the implementation worktree's ignored directory:

```text
.superpowers/sdd/2026-10-21-harness2-deepagents/
```

The ledger has early Docker-unavailable entries and an initial “all tasks pending” sentence; **the later commits and this handoff supersede those statements**. Tasks 4/5/8/9 were not formally marked complete while integration was still pending. Preserve this directory until the remaining verification/fixes are done.

Implementation decisions to retain unless deliberately revised:

- Execute the approved plan inline; one fresh whole-branch review has now completed.
- Use a dedicated `gateway.py` module for credential isolation.
- Use the public `SummarizationMiddleware` constructor for explicit trigger/keep values; its defaults factory does not accept them.
- One Pi container/private Durable storage directory per edit job.
- Parallel mode currently means independent search width; stage dispatch is serial. Cost: higher wall time, not a different planned rollout count.
- Initial evaluator supports exact text/JSON assertions. Upstream adapters and arbitrary grading commands are follow-ons.
- Named subagent delegation currently has one implemented level. Memory retrieval currently bounds startup memory files by sorted selection, not learned semantic retrieval.
- Preserve the ignored plan workspace while verification is incomplete.
- Direct approved uv lock/sync commands work with the `/tmp` cache. Early ad hoc environment/proxy overrides caused a macOS system-configuration panic; avoid those overrides.

No model quality improvements, benchmark parity scores, or production readiness claims have been established. Finish the seven review fixes before the authorized-budget live smoke test, then decide integration/merge with the user.
