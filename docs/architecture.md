# Architecture

How this repository is designed and how a run actually moves. It is a local research clone of [Harness²](https://github.com/google-research/harness2): the model stays fixed, and the system around it is what gets edited.

The model for every role is `accounts/fireworks/models/deepseek-v4p1-flash`. The controller, evaluator, credentials, isolation rules, and spending caps stay outside the editable surface.

## System at a glance

Three product surfaces share one runtime. `harness-clone run` is the generic task stream. `harness-clone orderops` is the scored self-improvement experiment. `harness-clone portfolio` and the Vite UI are the legacy portfolio cockpit; they call the same controller.

```text
Host process
+-----------------------------------------------------------------------+
| Portfolio UI (Vite and FastAPI)                                       |
|        |                                                              |
|        v                                                              |
| harness-clone CLI --> LangGraph controller (controller.py)            |
|                            |            |              |              |
|                            v            v              v              |
|                     Experience     run.sqlite     budget.sqlite       |
|                     library        + bundles                          |
|                            ^  audit -> consolidate -> wiki            |
|                            |  (offline, no model calls, no spend)     |
|                     dreaming.py                                       |
+----------------------------|---------------|--------------------------+
                             |               |
              execute bundle |               | code-bearing proposal
                             v               v
   Per-rollout Docker                 Per-edit Docker
   +----------------------------+     +----------------------------+
   | Executor container         |     | Pi Durable worker          |
   | Deep Agents                |     | workers/pi                 |
   |   | scoped token           |     |   | scoped token           |
   |   | internal network       |     |   v                        |
   |   v                        |     | Gateway for the edit job   |
   | Inference gateway          |     +-------------|--------------+
   | holds FIREWORKS_API_KEY    |                   |
   +-------------|--------------+                   |
                 |                                  |
                 +----------------+-----------------+
                                  | metered chat completions
                                  v
                             Fireworks

run.sqlite -- artifacts only, after the run --> Offline evaluator
                                                 evaluate.py or orderops score
                                                        |
                                                        | scores never re-enter the loop
                                                        x
                                                 controller
```

The host process reads `FIREWORKS_API_KEY` from the shell or from `.env` in the working directory. A shell value wins. The host never gives a candidate the account key or the `.env` file. Each rollout gets a one-shot gateway token. The executor container sits on an `--internal` Docker network. The gateway is the only process with public egress, and it accepts only the pinned model and a fixed set of request fields.

## What a task run does

`run_stream` walks tasks in ordinal order inside each domain. Each task calls `run_task`, which compiles one LangGraph:

```text
[Task and base bundle]
          |
          v
[References]
 execute the base harness k times
          |
          v
[Proposals] <----------------------------+
 INV and DOM editors                     |
          |                              | yes: sequential mode
          v                              | and rounds remain
[Probes]                                |
 execute each candidate                  |
          |                              |
          v                              |
[Compose]                               |
 merge, revise, or keep the parent       |
          |                              |
          v                              |
[Finals]                                |
 execute the composed bundle             |
          |                              |
          v                              |
 Sequential mode and rounds left? -------+
          |
          | no
          v
[Select one delivered output]
          |
          v
[Reflect]
 cue, lesson, evidence refs
          |
          v
(Experience library: earlier ordinals only)
          .
          . snapshot for the next task
          v
[Proposals of the next task]
```

| Stage | Label | What it changes |
|---|---|---|
| Reference | `base1` … `basek` | Nothing. Same parent bundle, k independent executions. |
| Propose | `invN`, `domN` | INV edits general procedure. DOM edits domain checks and practitioner knowledge. Siblings share a frozen stage list and do not see each other. |
| Probe | same labels | Execute the published candidate. |
| Compose | `aggN` in parallel, `cN` in sequential | One editor sees both proposals and their execution evidence. |
| Final | same labels | Execute the composed bundle. Sequential mode feeds that digest back as the next round's parent. |
| Select | — | Research profile: composed outputs only. Practical profile: baseline outputs are eligible too. |
| Reflect | — | Optional. A lesson is stored only when its evidence references validate. |

Parallel mode widens search from one parent (`k` INV, `k` DOM, `k` compositions). Sequential mode carries the composed bundle forward for `k` rounds. Stage dispatch inside a task is serial. Resume reads completed executions, edits, and selections from `run.sqlite` and does not repeat them. A changed task stream, source snapshot, config, base digest, or image ID needs a new run directory.

Invalid proposals get one repair attempt, then the parent is kept (`fallback`). `keep_base` is an explicit empty patch. Accepted patches are materialized, validated, and published by SHA-256 digest before anything executes them.

## Memory: capture in session, dream offline

Memory has two passes, after Anthropic's research (see References): an
in-session capture — the hippocampus — and an offline pass, dreaming, that
later verifies, corrects, and republishes what future tasks read. Reflect
writes; the dream edits. Neither ever deletes: the run store is append-only.

### Pass 1: reflect (in-session, during evolution)

Only runs with `learn=True` (portfolio evolve, `run` with learning) reflect,
and only after an adopted output exists:

```text
run_stream(learn=True), per task with a chosen execution
          |
          v
build_evidence(task, executions, library)   frozen, ordinal-scoped snapshot
          |
          v
editor.reflect(evidence, result)            one Deep Agents call
          |   salience steer: state the rule, HOW it is verified,
          |   warn about the trap; do not store re-derivable state
          v
validate_refs(draft.evidence_refs, evidence)   every ref must resolve
          |
          | rejected --> run.sqlite kind='reflection' as {"error": ...}
          v accepted
ExperienceEntry: cue / lesson / evidence_refs / confidence
          |
          v
library.append()          bundle digest must already be published
          |
          v
run.sqlite kind='experience'            append-only, causal
          |
          v
snapshot(namespace, domain, ordinal)    earlier ordinals only,
          .                             served to the next task's editors
          .
          v
```

### Pass 2: dream (offline; free unless --distill)

`harness-clone dream --run-dir <dir> [--base-dir <portfolio>] [--adopt]
[--distill --max-usd <cap>] [--verify --max-usd <cap>] [--organize --max-usd <cap>]`,
or `POST /api/dream` from the cockpit. No model calls, no spend,
deterministic — unless `--distill` / `--verify` / `--organize` opt into
metered analyzer calls:

```text
     run store (run.sqlite)                    adopted bundle
          |                                         |
          v                                         |
+------------------------+                          |
| 0. distill (--distill) | one metered structured   |
|  full-trace hindsight  | call per unanalyzed      |
|  transcript; citations | transcript; refs must    |
|  must resolve; secrets | resolve IN the analyzed  |
|  dropped; per-input    | trace; entries get       |
|  accounting; converge  | origin="distilled"       |
+-----------+------------+                          |
            |                                       |
            v                                       |
+------------------------+                          |
| 0b. semantic audit     | one metered structured   |
|  (--verify) per-entry  | call per unverified      |
|  verdict: supported /  | lesson vs the FULL traces|
|  contradicted ->       | its citations point into;|
|  governed correction   | verdicts persist, so     |
|  (rebuttal refs must   | dreams converge; stale   |
|  resolve; else marked  | bundles verified first   |
|  inconclusive)         |                          |
+-----------+------------+                          |
            |                                       |
            v                                       |
+-----------+------------+                          |
            |                                       |
            v                                       |
+------------------------+                          |
| 1. audit_library       | every evidence ref       |
|    per-entry status:   | re-resolved against      |
|  verified / intact /   | stored executions;       |
|  broken-ref / stale /  | bundle digests checked   |
|  duplicate / conflict/ | against the adopted      |
|  secret / superseded   | digest                   |
+-----------+------------+                          |
            |                                       |
            v                                       |
+------------------------+                          |
| 2. consolidate         | append-only restatements |
|  duplicates  -> merge  | supersedes the originals |
|  later correction      | (superseded entries stay |
|  disagreement->CONFLICT| as audit stubs; dream    |
|  verified single       | entries never restated)  |
+-----------+------------+                          |
            |                                       |
            v                                       v
+--------------------------------------------------+---------+
| 3. build_memory_files                                     |
|  memory/<domain>.md: current understanding + provenance   |
|  memory/AGENTS.md: standing text kept, index regenerated  |
|  count capped by retrieval_limit; pages split so no       |
|  single file exceeds memory_budget_tokens*4 chars —       |
|  lessons are never truncated                              |
+--------------------------------------------+---------------+
                                             |
                                             v
+------------------------+                          |
| 0c. organize           | one metered structured   |
|  (--organize) per-PAGE | call per wiki page;      |
|  rewrite: sections,    | rewrite replaces only    |
|  cross-links, promote  | that page's body; cached |
|                        | by raw-page digest, so   |
|                        | unchanged dreams recall  |
|                        | it for free              |
+-----------+------------+                          |
            |                                       |
            v                                       |
+------------------------+                          |
| 0d. link repair (free) | [[alias]] repointed to   |
|  every dream           | the canonical page;      |
|                        | dangling brackets dropped|
|  hand-written standing |                          |
|  text never touched    |                          |
+-----------+------------+                          |
            |                                       |
            v                                       v
4. HarnessPatch (memory files only) -> apply_patch -> publish digest
                                             |
                                --adopt -> archive + adopted.json
```

### The memory hierarchy

The wiki is the small injected top layer the research describes; detail is
one link away, and full history stays in the store:

```text
 injected at startup, bounded by retrieval_limit and
 memory_budget_tokens (chars/4 ~ tokens, index preloads first)
 +------------------------------------------------------+
 | memory/AGENTS.md                                     |
 |   hand-written standing text (never regenerated)     |
 |   + dream index block: [[portfolio]] -> file         |
 +----------------------------|-------------------------+
                              | one link away
 +----------------------------v-------------------------+
 | memory/portfolio.md                                  |
 |   plain statements of current understanding, with    |
 |   provenance: cue, verified date or unverified,      |
 |   task, bundle, "stale: adopted bundle is ..."       |
 +----------------------------|-------------------------+
                              | full history
 +----------------------------v-------------------------+
 | run.sqlite                                           |
 |   every entry ever captured, superseded stubs        |
 |   included; snapshots stop serving superseded keys   |
 +------------------------------------------------------+
```

### Research-to-code mapping

| Concept (Anthropic) | Here |
|---|---|
| Transcripts as ground truth | Execution records + traces in `run.sqlite` |
| In-session salience tagging | `editor.reflect()` (Pass 1) |
| Dream verifies memory against transcripts | `audit_library` re-resolving every ref; `--verify` adds the semantic judgment (`semantic.py`); `--organize` rewrites each wiki page with cached, metered calls (`organize.py`) |
| "How it is known" / validated rules | `verified_on` date + `confidence="verified"` |
| Later correction of a stale rule | Correction restatement keeps the newest lesson and notes what it replaced (source, date, bundle) |
| Conflicts: keep both sides, never guess | One `CONFLICT` note quoting every side; snapshots serve the note, not either side — a semantic-audit correction needs resolving rebuttal refs too |
| Duplicates merged with stubs | Merge restatements supersede the group; store keeps the originals |
| Wiki files + `_index.md` catalog | `memory/<domain>.md` + regenerated `AGENTS.md` index; `[[slug]]` links repaired for free every dream (aliases repointed, dangling dropped) |
| Small injected top layer | Count bounded by `retrieval_limit`, size by `memory_budget_tokens` (index first, over-budget pages named for on-demand reading) |
| Never copy secrets into memory | Credential-shape scan drops them from the wiki; store copies stay auditable |
| Deterministic dreams that converge | Revisioned keys; dream entries are never restated; unchanged state republishes the same digest |
| Failed nights retry, cadence is offline | The dream is free; run it after any evolve week, or never |
| Adoption stays governed | Output is a candidate bundle; `--adopt` is explicit |

Guarantees that fall out of the shape: the budget ledger is untouched, the
dream can never edit graders, tasks, or controller code (it flows through
bundle validation like anything else), and repeated dreams converge instead
of rewriting memory forever.

Live distillation (`--distill`), the semantic audit (`--verify`), and the
organizer pass (`--organize`) are the metered stages of the cookbook
workflow: full transcripts are re-read with hindsight for process lessons
the in-session reflect could not see, existing lessons are judged against
the transcripts they cite, and wiki pages are rewritten around their
lessons with cross-links. Every observation must cite rows that exist in
the analyzed trace; a correction needs rebuttal refs that resolve too;
an organizer rewrite replaces only its own page's body. All three flow
through the same deterministic audit/consolidate/wiki pipeline as session
lessons (see `docs/dreaming.md`, sections 5–7; design history in
`docs/superpowers/specs/2026-11-09-comprehensive-dreaming.md`).

## Editable bundle

A harness version is a directory, never a live object in the controller. `bundles.py` rejects symlinks and any path outside this surface. `compiler.py` turns a published tree into a Deep Agents graph inside the worker, not in the controller.

```text
manifest.json          schema, parent digest, enabled components
config.json            context, compaction, retrieval, delegation depth
system.md              system prompt
guardrails.md          task-behavior guidance
memory/*.md            startup memory, bounded by retrieval_limit and
                       memory_budget_tokens (chars/4 ~ tokens)
tools/descriptions.json
subagents/*.json       same Fireworks model, one delegation level
skills/<name>/SKILL.md
scripts/<name>.py
middleware/<name>.py   loaded only in the worker
```

The compiler copies memory, skills, and scripts into the worker workspace, appends guardrails to the prompt, wraps tools with the versioned descriptions, and installs a shared tool-call limit plus summarization middleware. Private scratch and `/output` are mounts. The bundle mount is read-only. Runtime memory writes do not change the published digest.

## Editors

`ExperimentConfig.editor_policy` picks the editor. Both return an `EditProposal` (patch, rationale, evidence refs, or `keep_base`).

| Policy | Where it runs | When it is used |
|---|---|---|
| `text` | Host-side Deep Agents editor. Filesystem edits only. | Demos and runs that must not change code. |
| `pi` (default for live `run`) | One Pi Durable container per edit job, with its own SQLite session under `run-dir/pi/`. | Proposals that may edit scripts, middleware, and other code-bearing files. |

Pi is given an allow-list of the same paths the bundle validator accepts. The controller still diffs the candidate against the parent and republishes only a validated patch. Pi does not select winners, write the ledger, or see hidden answers.

## Isolation and spend

```text
Trusted host
+------------------------------------------------------------------+
| FIREWORKS_API_KEY in the shell                                   |
|          |                                                       |
|          |                          max_usd and per-stage        |
|          |                          ceilings                     |
|          |                               |                       |
|          |                               v                       |
|          |                    (budget.sqlite reservations)       |
|          |                               |                       |
|          v                               v                       |
|   +----------------------------------------------+               |
|   | Gateway                                      |               |
|   | fixed upstream, no URL forwarding            |               |
|   +----------------------+-----------------------+               |
+-------------------------|-------------------------+--------------+
                           |
                           | chat completions
                           v
                      Fireworks

Untrusted candidate container
+------------------------------------------------------------------+
| Deep Agents rollout                                              |
|      |                |                    |                     |
|      v                v                    v                     |
|  /source           /bundle              /output                  |
|  read-only         read-only            deliverables             |
|  task files                                                      |
+-------------------------|-------------------------+--------------+
                           |
                           | HARNESS_GATEWAY_TOKEN
                           v
                        Gateway (above)
```

Each planned phase (`base`, `inv`, `dom`, compose) is allocated on the ledger before proposals start, so early edits cannot consume the budget reserved for composition and the final execution. Every upstream call reserves a conservative cost, then settles actual token usage. If the gateway dies after admission, unsettled calls are marked unknown and block further paid work. The executor image is non-root, read-only, capability-dropped, and memory/CPU/pid limited. OrderOps also strips the case generator and grader out of that image (`containers/strip_orderops_evaluation.py`).

## OrderOps experiment

OrderOps is the check that editing the harness changes held-out behavior. Case generation writes public tasks and private `answers.json` into disjoint directories. The online runner does not import the generator or the grader.

```text
orderops generate
   |
   +-- dev cases
   +-- holdout cases
   +-- private answers.json ---------------------------+
                                                       |
dev cases (one shared ledger)                          |
   |                                                   |
   +-- frozen       base harness, one shot             |
   |      |                                            |
   |      +-- holdout: one frozen execution -----------+
   |                                                   |
   +-- local        Pi refine, reset each case         |
   |      |                                            |
   |      +-- last local bundle                        |
   |             |                                     |
   |             +-- holdout: one frozen execution ----+
   |                                                   |
   +-- persistent   carry bundle and lessons           |
   |      |                                            |
   |      +-- last adopted bundle                      |
   |             |                                     |
   |             +-- holdout: one frozen execution ----+
   |                                                   |
   +-- retries      base harness, matched attempts     |
          |                                            |
          +-- holdout: one frozen execution -----------+
                                                       |
                                                       v
                                             orderops evaluate
                                             report outside the
                                             run directory
```

`local` and `persistent` call `run_task` (practical profile, sequential mode). `frozen` and `retries` skip editing and execute the base bundle directly. `retries` matches the local arm's execution count, not its dollar spend, because editing, selection, and reflection are extra. Selection is model judgment over delivered artifacts. Hidden scores are applied only by `orderops evaluate`, and that report is written outside the run directory so it cannot become a task source.

`verify_live_rsi` checks a finished run for accepted or `keep_base` INV, DOM, and COMPOSE stages plus a persistent reflection entry. It does not grade answer quality.

## Portfolio cockpit

The portfolio path is a second caller of the same loop, not a second optimizer.

1. `portfolio fetch` builds a dated snapshot task from a holdings CSV, plus market context. No model call.
2. `portfolio daily` executes the adopted bundle once in the executor container and writes a recommendation report.
3. `portfolio evolve` runs a sequential stream over the week's snapshot tasks and archives the adopted digest.
4. `portfolio score` grades past calls from later prices. It does not feed scores back into evolution.

The UI (`ui/` plus `ui_server.py`) reads snapshots and the scoreboard, and starts those CLI commands as jobs. It does not embed the controller. The same API also serves the memory layer:

- `GET /api/memory` — the wiki the adopted bundle serves at startup.
- `POST /api/dream` — runs the dream on the newest run store with experiences (or one you name); `"adopt": true` adopts the resulting wiki bundle. Deterministic and free, so it runs synchronously.

The cockpit panel for this is `ui/src/components/MemoryPanel.tsx`. Demo data ships dream-ready: `scripts/seed_demo.py` seeds real evolve-week stores (published base-harness bundle, traced executions, experiences with duplicates, corrections, and a broken ref).

## Records on disk

A run directory is the unit of resume and of identity.

| Path | Role |
|---|---|
| `run.sqlite` | Experiment config, tasks, executions, edits, selections, reflections, experience entries, dream provenance, OrderOps admission. |
| `budget.sqlite` | Reservations, settlements, unknown usage, phase allocations. |
| `bundles/<digest>/` | Immutable published harness trees, including the memory wiki once a dream has been adopted. |
| `adopted.json` (portfolio base dir) | Which archived digest daily/evolve runs and the cockpit start from. |
| `inputs/` | OrderOps source snapshot copied at admission. |
| `pi/<request>/` | Pi job context, candidate tree, and Durable session state. |

Experience entries are never updated in place. A dream restatement is a new entry with `origin="dream"`, a bumped `revision`, `verified_on` when its evidence re-resolved, and `supersedes` naming the keys it replaces; snapshots stop serving superseded keys but the store keeps them.

Offline `evaluate` compares delivered artifacts with an expectations file and refuses to write its report inside any persisted task source directory.

## Code map

| Concern | Module |
|---|---|
| CLI | `src/harness_clone/cli.py` (`demo`, `run`, `resume`, `inspect`, `evaluate`, `watch`, `doctor`, `dream`), `orderops/cli.py` |
| Task graph | `controller.py` |
| Cross-task lessons: capture | `experience.py` (library, snapshots, `run_stream`) |
| Cross-task lessons: dreaming | `dreaming.py` (audit, consolidate, wiki, publish) |
| Bundle validation and publish | `bundles.py` |
| Deep Agents compilation | `compiler.py`, `worker.py` |
| Container rollout | `sandbox.py` |
| Editors (text and Pi) | `editors.py`, `pi_editor.py`, `pi_client.py`, `workers/pi/` |
| Evidence given to editors | `evidence.py` |
| Credential boundary and metering | `gateway.py`, `providers.py`, `storage.py`, `envfile.py` |
| Task sources and contracts | `tasks.py`, `contracts.py` |
| OrderOps protocol | `orderops/experiment.py`, `cases.py`, `score.py` |
| Generic offline grader | `evaluate.py` |
| Portfolio subsystem | `portfolio_ops.py` (daily/evolve), `portfolio_snapshot.py`, `portfolio_csv.py`, `portfolio_market.py`, `portfolio_history.py`, `portfolio_score.py`, `portfolio_artifacts.py` |
| Cockpit API and UI | `ui_server.py`, `ui/` (Vite + React), `monitor.py` (read-only HTML view) |
| Scripted demo, no model | `demo.py`, `scripts/seed_demo.py`, `scripts/dream_demo.py` |
| Capability checks | `preflight.py` |

## Testing map

| Layer | Tests |
|---|---|
| Dream (audit, consolidate, wiki, convergence) | `tests/test_dreaming.py` |
| Cockpit API incl. `GET /api/memory`, `POST /api/dream` | `tests/test_ui_server.py` |
| Bundle validation, patches, snapshots | `tests/test_bundles.py`, `tests/test_portfolio_cli.py` |
| Offline demo and CLI flows | `tests/test_demo.py`, `tests/test_cli.py` |

Full suite: `uv run pytest -q`; the dream demo needs no API key (`scripts/dream_demo.py`).

## References

Sources this code was built from:

- Harness² (Google Research), https://github.com/google-research/harness2 — the original system cloned here at research scale.
- `docs/harness2-deepagents-proposal.md` — the design note that became this codebase.
- OrderOps protocol: `docs/superpowers/specs/2026-10-23-orderops-design.md`; the portfolio agent spec is `docs/superpowers/specs/2026-10-21-self-evolving-portfolio-agent-design.md`.
- Anthropic, "The components of agent memory" (November 2026), https://www.anthropic.com/research/components-of-agent-memory — the two-pass memory model (in-session capture + offline dreaming), the wiki representation, the small injected top layer, and the wrong-note loop that motivated verifying memory against transcripts.
- Anthropic, "Dreaming with a dynamic workflow" (Managed Agents cookbook), https://platform.claude.com/cookbook/managed-agents-cma-dreaming-with-a-dynamic-workflow — the analyzer/organizer workflow the deterministic dream mirrors: analyzers per transcript, one organizer pass, transcripts as data never instructions, convergence on repeat runs.
- LangChain Deep Agents — the agent runtime `compiler.py` targets inside the executor container.
- Pi Durable (`workers/pi`) — the isolated editing worker for code-bearing proposals.
