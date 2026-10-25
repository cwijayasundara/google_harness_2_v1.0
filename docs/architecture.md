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

## Editable bundle

A harness version is a directory, never a live object in the controller. `bundles.py` rejects symlinks and any path outside this surface. `compiler.py` turns a published tree into a Deep Agents graph inside the worker, not in the controller.

```text
manifest.json          schema, parent digest, enabled components
config.json            context, compaction, retrieval, delegation depth
system.md              system prompt
guardrails.md          task-behavior guidance
memory/*.md            startup memory, bounded by retrieval_limit
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

The UI (`ui/` plus `ui_server.py`) reads snapshots and the scoreboard, and starts those CLI commands as jobs. It does not embed the controller.

## Records on disk

A run directory is the unit of resume and of identity.

| Path | Role |
|---|---|
| `run.sqlite` | Experiment config, tasks, executions, edits, selections, experience, OrderOps admission. |
| `budget.sqlite` | Reservations, settlements, unknown usage, phase allocations. |
| `bundles/<digest>/` | Immutable published harness trees. |
| `inputs/` | OrderOps source snapshot copied at admission. |
| `pi/<request>/` | Pi job context, candidate tree, and Durable session state. |

Offline `evaluate` compares delivered artifacts with an expectations file and refuses to write its report inside any persisted task source directory.

## Code map

| Concern | Module |
|---|---|
| CLI | `src/harness_clone/cli.py`, `orderops/cli.py` |
| Task graph | `controller.py` |
| Cross-task lessons | `experience.py` |
| Bundle validation and publish | `bundles.py` |
| Deep Agents compilation | `compiler.py`, `worker.py` |
| Container rollout | `sandbox.py` |
| Credential boundary and metering | `gateway.py`, `providers.py`, `storage.py` |
| Pi editing | `pi_editor.py`, `pi_client.py`, `workers/pi/` |
| Evidence given to editors | `evidence.py` |
| OrderOps protocol | `orderops/experiment.py`, `cases.py`, `score.py` |
| Generic offline grader | `evaluate.py` |
| Scripted demo, no model | `demo.py` |

The original design note is `docs/harness2-deepagents-proposal.md`. The OrderOps protocol is `docs/superpowers/specs/2026-10-23-orderops-design.md`. This file describes the code those documents turned into.
