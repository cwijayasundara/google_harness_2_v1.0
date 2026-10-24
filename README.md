# Harness Clone

A local research MVP for recursively optimizing a Deep Agents harness with Fireworks DeepSeek V4.1 Flash and Pi Durable code editing.

The optimizer versions system prompts, guardrails, memory, tool guidance, subagents, skills, scripts, middleware, and declared harness settings. The controller, evaluator, credentials, isolation, and spending limits stay outside its editable surface.

## Status

The deterministic workflow, real Deep Agents compilation, persistence, causal experience, Python Pi client, and Pi Durable SQLite reopen behavior are tested locally. Docker execution is verified by the explicitly enabled Docker tests (read-only/non-root/no-public-egress isolation and a real Deep Agents graph executing a candidate script in a container). Live Fireworks preflight (tool round trip, streaming, structured output, usage metadata) passes against the real provider, and one complete live experiment run (text editor, k=1) was verified on 21 October 2026: all four executions delivered and the offline evaluation passed, for about $0.08. Complete Pi code-edit behavior over the real provider and live portfolio runs are still unverified. The demo uses scripted editors and deterministic Python computations; it demonstrates scheduling and storage, not model quality improvement.

## How to run

A step-by-step path from a fresh clone to a live run. Steps 1–4 are free; steps 5–7 make paid Fireworks calls (except `portfolio fetch` and `portfolio score`) and need a spending cap you choose. Run everything from the repository root.

**1. Install** (Python 3.12, [uv](https://docs.astral.sh/uv/), Node >= 22.19.0, Docker Desktop or another Docker daemon):

```bash
uv sync --frozen
npm ci --prefix workers/pi
```

**2. Check the install offline** (no Docker, network, or key needed):

```bash
uv run ruff check .
uv run pytest -q                       # expect 304 passed, 4 skipped
uv run harness-clone doctor            # offline environment report
uv run harness-clone demo --run-dir runs/demo-parallel --mode parallel --k 2
uv run harness-clone inspect --run-dir runs/demo-parallel
```

**3. Build the container images and check isolation** (Docker running; still free). Rebuild after any source change. Live runs pin image IDs, so a rebuilt image needs a fresh `--run-dir`:

```bash
docker compose -f containers/compose.yaml --profile build build
HARNESS_TEST_DOCKER=1 uv run pytest -m docker -q     # expect 4 passed
```

**4. Set credentials in your shell** (host environment only; the app never reads `.env`):

```bash
export FIREWORKS_API_KEY=...    # required for every paid command
export ETORO_API_KEY=...        # eToro application key, only for `portfolio fetch/daily` without --csv
export ETORO_USER_KEY=...       # eToro user key, paired with the application key
```

**5. Live preflight** (costs well under $0.01):

```bash
uv run harness-clone doctor --live --max-usd 2    # expect "status": "live-pass"
```

**6. Run a harness-optimization experiment** on the example task, then score it offline:

```bash
uv run harness-clone run \
  --task examples/tasks/summary/task.json \
  --base-harness examples/base-harness \
  --run-dir runs/live-summary \
  --mode parallel --k 1 --profile research \
  --editor-policy text --max-usd 2
uv run harness-clone inspect --run-dir runs/live-summary   # expect "errors": [] and recorded spend

echo '{"summary": {"answer.json": {"count": 3, "total": 10}}}' > /tmp/expectations.json
uv run harness-clone evaluate --run-dir runs/live-summary \
  --expectations /tmp/expectations.json --output /tmp/live-results.json
```

Use `--task` more than once for a multi-task stream, `--k` to widen the search, `--mode sequential` to carry each composed harness forward, and `--editor-policy pi` (the default) to let Pi Durable make code-bearing edits. Re-running the same command resumes; `uv run harness-clone resume --run-dir runs/live-summary` replays nothing that already finished.

**7. Test self-evolution with OrderOps** (see the full workflow below):

```bash
uv run harness-clone orderops generate \
  --tasks-dir runs/orderops/tasks --private-dir runs/orderops/private
```

Generation is free. Live evolution needs Docker, Fireworks credentials, and your
explicit total spending cap. Hidden answers are used only by the separate offline
evaluator.

**Troubleshooting**

- `no baseline execution delivered`: the run reached Docker but the baseline failed. Read the execution records with `sqlite3 <run-dir>/run.sqlite "select id, value from records where kind='execution'"`; `worker-exit` with zero tokens means the gateway refused the request before any paid call.
- `stream inputs changed` or `incompatible experiment configuration`: the task, sources, images, or `--max-usd` differ from what that run directory started with. Use a new `--run-dir`.
- `live execution requires a key and nonzero spending cap`: export `FIREWORKS_API_KEY` and pass a nonzero `--max-usd`.
- Images rebuilt mid-experiment: start a fresh `--run-dir`; resume rejects changed image IDs by design.

## Install and try the offline demo

Use Python 3.12, uv, Node >=22.19.0, and Docker for live workers. Run from the repository root:

```bash
uv sync --frozen
npm ci --prefix workers/pi
uv run harness-clone doctor
uv run harness-clone demo --run-dir runs/demo-parallel --mode parallel --k 2
uv run harness-clone demo --run-dir runs/demo-sequential --mode sequential --k 2
uv run harness-clone inspect --run-dir runs/demo-parallel
uv run harness-clone resume --run-dir runs/demo-parallel
```

Both demos process two ordered tasks. At k=2 each task has eight executions and six editing stages. Each run retains adopted bundles, evidence references, and supported lessons. Repeating the command resumes compatible completed work. A changed model, task stream, source content, or experiment configuration requires a new run directory.

## Live execution prerequisites

Start Docker and build the images from the repository root:

```bash
docker build -f containers/executor.Dockerfile -t harness-clone-executor:0.1 .
docker build -f containers/pi.Dockerfile -t harness-clone-pi:0.1 .
```

Set `FIREWORKS_API_KEY` in the **host environment**. The application does not load `.env` automatically. Do not place a key in task sources, candidate bundles, or committed configuration.

Paid calls require an explicit nonzero cap. The following commands are examples; choose your own budget before running them:

```bash
uv run harness-clone doctor --live --max-usd 2
uv run harness-clone run \
  --task examples/tasks/summary/task.json \
  --base-harness examples/base-harness \
  --run-dir runs/live-summary \
  --mode parallel --k 1 --profile research \
  --editor-policy pi --max-usd 2
```

The model is fixed to `accounts/fireworks/models/deepseek-v4p1-flash`. Every execution/editor/subagent/summary role uses it. The gateway meters calls against a shared SQLite ledger and stops further paid work if usage is unknown or the configured cap is exhausted. Published token prices are embedded as estimates; verify current rates before using the cost cap as a financial control. A reservation conservatively estimates the next request; actual provider billing may exceed estimates and is recorded rather than discarded.

Live doctor checks cover a tool round trip, structured JSON, streaming, and usage metadata. Executor compaction/restart and complete Pi code-edit behavior require separate container-backed validation; doctor never declares those verified.

## OrderOps: test whether the harness improves

OrderOps investigates synthetic order refunds and warehouse discrepancies. Every
case has an immediately checkable answer. The model stays fixed while Pi edits
prompts, skills, scripts and other harness files. Changed files alone do not prove
improvement: compare frozen harnesses on held-out cases using the offline grader.

Generate 30 development and 20 held-out cases (free, no credentials):

```bash
uv run harness-clone orderops generate \
  --tasks-dir runs/orderops/tasks --private-dir runs/orderops/private --seed 42
```

Public task sources and private answer keys occupy disjoint directories. The executor
image excludes the generator and grader from its source and installed packages. Use new
paths to regenerate; the command refuses existing destinations. Start with
`--dev-count 6 --holdout-count 2` for a small operational smoke test; this is too
small to support a quality claim.

Build the images after source changes, export `FIREWORKS_API_KEY`, and choose a
spending cap. This command deliberately requires you to set `ORDEROPS_MAX_USD`:

```bash
docker compose -f containers/compose.yaml --profile build build
uv run harness-clone orderops run \
  --tasks-dir runs/orderops/tasks --run-dir runs/orderops/experiment \
  --max-usd "${ORDEROPS_MAX_USD:?Set your total experiment spending cap}"
```

The default arms are:

| Arm | Development behavior | Held-out behavior |
|---|---|---|
| frozen | Original harness, one execution per case | Original harness, one execution per case |
| local | Pi refinement; reset bundle and lessons per case | Freeze the final development case's selected bundle |
| persistent | Pi refinement; carry selected bundles and earlier lessons forward | Freeze the final adopted bundle |
| retries | Original harness, local-matched attempts with model selection | Original harness, one execution per case |

Use `--arms frozen persistent` to run a subset. Defaults are Pi editing, k=1,
sequential refinement, and practical selection (baseline outputs are eligible).
Retries match actual local execution counts (or scheduled 4 × k if local is omitted),
not total dollars: refinement also spends on
editing, selection, and reflection. All arms share one capped ledger. There is no
guaranteed gain and no hidden score is used to pick candidates. With these defaults,
a full 30/20 suite schedules 470 execution attempts plus editor calls; select a
small suite first. Repeat the same run command to resume finished work. Changed
sources, settings, base bundle or image IDs require a fresh run directory.

Evaluate separately, with the report outside the online run directory:

```bash
uv run harness-clone orderops evaluate \
  --run-dir runs/orderops/experiment --answers runs/orderops/private/answers.json \
  --output runs/orderops/evaluation.json
```

The report includes development and held-out success, finding/action accuracy,
false active actions, delivery coverage, row-reference validity, execution cost
and elapsed execution time, paired held-out outcomes, and total ledger spending.
Row-reference validity checks that a cited row exists; it does not prove the row
supports the finding. Narrative quality is ungraded. Pilot results are descriptive.
Held-out families include transaction-state transitions and renamed CSV columns.

The [design](docs/superpowers/specs/2026-10-23-orderops-design.md) describes the
contracts, trust boundaries, and experiment protocol. The generic `run` command
still uses its original experience-library behavior; explicit cross-task bundle
carry-forward is implemented by the OrderOps experiment runner.

The portfolio seed example has been retired and removed from `examples/`.
Legacy portfolio commands and UI code remain, and previously archived adopted
bundles can still be used. A new portfolio daily/evolution run without an archived
bundle reports the retirement. Test fixtures are not shipped as application seeds.

## How refinement works

Research mode runs k baseline executions, k general INV proposals and k practitioner DOM proposals, their probes, and k composed executions. Sequential mode uses one INV/DOM pair per round and carries the composed harness forward. Parallel mode creates all candidates from shared baseline evidence. The current controller dispatches independent stages serially; its parallel mode describes search width, not simultaneous execution. Cross-domain task streams can be processed concurrently through the Python API.

Selection chooses an existing composed output. Practical mode additionally allows baseline outputs. Invalid proposals receive one repair attempt before a separately recorded fallback. A valid unchanged proposal is recorded as KEEP_BASE. No online stage receives offline evaluation scores.

`--editor-policy pi` routes the complete INV/DOM/COMPOSE stage through Pi under a fixed policy, including code-bearing changes. `--editor-policy text` uses a filesystem-only Deep Agent editor and rejects executable changes. Selection and reflection use filesystem-only Deep Agents in both policies.

## Author a harness

Start with `examples/base-harness/`:

| File/category | Meaning |
|---|---|
| `manifest.json` | Schema, enabled components, provenance |
| `system.md` | Primary execution instructions |
| `guardrails.md` | Editable task-behavior guidance |
| `memory/*.md` | Versioned memory seeds, copied into a private runtime overlay |
| `tools/descriptions.json` | Tool descriptions; argument schemas remain fixed |
| `subagents/*.json` | Name, description, system prompt; same explicit Fireworks model |
| `skills/<name>/SKILL.md` | Discoverable skill with matching name/description frontmatter |
| `scripts/*.py` | Candidate-local Python utilities |
| `middleware/*.py` | `create_middleware(config)` returning LangChain `AgentMiddleware` |
| `config.json` | Context tokens, compaction fraction, memory-entry retrieval limit, delegation ceiling |

All executable plugins are imported in execution containers, never the controller. The current compiler provides one level of named delegation; zero disables it. Positive delegation ceilings constrain guidance; deeper recursive delegation is not implemented. Memory retrieval currently chooses a bounded sorted set of memory files at startup; replacing its strategy can be done through candidate middleware in a later extension.

Skills must be named in always-on instructions. Referenced scripts must ship in the same bundle. Absolute paths, path traversal, symlinks, invalid Python, malformed definitions, and out-of-surface edits are rejected. Omitted files are inherited; deletion is explicit. The operational tool-call limit and container resource/time limits are outside the candidate settings.

## Task sources and artifacts

A task JSON defines `id`, `domain`, `ordinal`, `prompt`, `source_root`, and `output_paths`. Relative source paths resolve beside the task file. Keep only authorized task inputs in the source directory. Tasks in one domain require unique IDs and ordinals. The first adapter supports text/JSON artifacts up to 1MB each and at most 2MB of source evidence. Binary document rendering and upstream benchmark adapters are follow-on work.

Task sources and the frozen bundle are read-only container mounts. Output and working memory are private writable mounts. Containers run as a non-root user with dropped capabilities and an internal network. A separate trusted gateway has upstream egress and the account credential. Shell access makes filesystem middleware insufficient as a security boundary; real Docker/network tests are mandatory before trusting isolation.

## Offline evaluation

Provide an external expectations JSON mapping task IDs to artifact names and expected strings or parsed JSON values. Run evaluation separately:

```bash
uv run harness-clone evaluate --run-dir runs/demo-parallel \
  --expectations /path/to/private-expectations.json \
  --output /path/to/offline-results.json
```

The evaluator refuses to write results inside the online run directory. Unscored tasks remain unscored and coverage is reported. This initial evaluator implements artifact assertions, not arbitrary grading commands or the upstream benchmark graders.

## Verification

Run setup and the free app/RSI checks with one command:

```bash
scripts/setup-and-test.sh
```

This installs the locked Python, Pi worker, and UI dependencies, runs lint and
the non-Docker Python/Pi tests, builds the worker and UI, then checks both
deterministic refinement schedules, resume, inspection, offline scoring, and
the real HTTP API serving the built UI. The Python suite includes OrderOps
bundle/lesson persistence and evaluation-isolation tests. Logs, JSON reports,
and run artifacts are retained in a fresh `runs/setup-test.XXXXXX` directory.
The script exits nonzero on a failed check and stops its temporary API server.

```bash
scripts/setup-and-test.sh --skip-setup  # reuse installed dependencies
scripts/setup-and-test.sh --docker     # also build images and test isolation
export FIREWORKS_API_KEY=...           # live mode only; shell environment
scripts/setup-and-test.sh --live --max-usd 2
```

Live mode implies Docker checks and runs all four OrderOps arms with Pi editing
on two development cases and one held-out case. `--max-usd` is the single shared
experiment cap; no separate paid preflight is run. Choose a cap appropriate to
your budget; exhaustion can leave missing deliveries and fail the check. The
separate offline evaluator saves `live-scores.json` outside the experiment.
Delivery, known spending, valid INV/DOM/COMPOSE edits in both refinement arms,
and persistent reflections must pass. An editor fallback fails the RSI check;
an explicitly validated KEEP_BASE is allowed. Diagnostics for missing structured
editor responses are saved as `editor-response` records in the experiment's
`run.sqlite`. Pi control traffic uses authenticated HTTP through `docker exec`
so workers need no published port on their isolated internal networks.
Quality scores are reported
without requiring an improvement. This small pilot does not establish a quality
gain. Default mode makes no paid calls; dependency installation needs network
access on a fresh machine. Install uv, Node >=22.19, and npm first; uv provisions
Python 3.12 as needed.

```bash
uv run ruff check .
uv run pytest -q
npm test --prefix workers/pi
npm run build --prefix workers/pi
HARNESS_TEST_DOCKER=1 uv run pytest -m docker -q
```

Docker tests are explicitly skipped without `HARNESS_TEST_DOCKER=1`; a skipped test is not a verified container boundary. Pi's Node SQLite adapter currently emits Node's experimental SQLite warning.

## Why Docker is required for live testing

Candidate harnesses are model-written and executable: they contain Python scripts and middleware, and the executing agent has shell tools. Running that code directly on the host would give untrusted generated code access to your files, network, and the host's `FIREWORKS_API_KEY`. Filesystem middleware alone cannot contain a process that can spawn a shell, so Docker is the isolation boundary, not an optional packaging step:

- Execution containers run as a non-root user with all capabilities dropped, a read-only bundle/source mount, and private writable output/scratch only.
- Workers sit on an internal Docker network with no public egress; they can only reach the inference gateway.
- The account API key exists only in the trusted gateway container. Workers and Pi editors receive a scoped gateway token, so generated code never sees the credential.
- The gateway is also the metering point: every provider call is reserved and settled in the shared budget ledger before untrusted code can spend.
- Images pin the runtime (Python, Deep Agents, Pi) so runs are reproducible; the CLI records the executor and Pi image IDs at admission and refuses to resume a run against rebuilt images.

The offline test suite, deterministic demos, and `doctor` without `--live` do not need Docker and make no paid calls. Docker is only needed for the isolation tests (`HARNESS_TEST_DOCKER=1`) and for any live `run`.

## Manual test checklist

Work from the repository root. Steps 1–4 are free; steps 5–7 make paid Fireworks calls and need a spending cap you choose.

1. **Set up and run the offline suite** (no Docker, no network, no key needed):

   ```bash
   uv sync --frozen
   npm ci --prefix workers/pi
   uv run ruff check .
   uv run pytest -q            # expect 304 passed, 4 skipped
   npm test --prefix workers/pi
   npm run build --prefix workers/pi
   ```

2. **Run the deterministic demo** (scripted editor/executor; verifies scheduling, storage, and resume without a model):

   ```bash
   uv run harness-clone demo --run-dir runs/demo-parallel --mode parallel --k 2
   uv run harness-clone inspect --run-dir runs/demo-parallel
   uv run harness-clone resume --run-dir runs/demo-parallel   # replays nothing; counts stay 16 executions / 12 stages
   ```

3. **Build the container images** (needed for steps 4 and 6). Rebuild after any source change; live runs pin image IDs, so a rebuilt image requires a fresh run directory:

   ```bash
   docker compose -f containers/compose.yaml --profile build build
   ```

4. **Verify the isolation boundary** (Docker required, still no paid calls):

   ```bash
   HARNESS_TEST_DOCKER=1 uv run pytest -m docker -q   # expect 4 passed
   ```

5. **Live preflight** — real Fireworks calls for tool use, streaming, and structured output; typically costs well under $0.01:

   ```bash
   export FIREWORKS_API_KEY=...   # host environment only; never commit it
   uv run harness-clone doctor --live --max-usd 2
   ```

   Expect `"status": "live-pass"` with `tool_call`, `tool_roundtrip`, `usage`, `stream`, and `structured` all true. Note `executor_compaction` stays `unverified` by design.

6. **Live experiment** — a full Propose–Probe–Compose run on the example task (Docker plus paid calls). Start with the text editor; try `--editor-policy pi` afterwards for the Pi Durable path:

   ```bash
   uv run harness-clone run \
     --task examples/tasks/summary/task.json \
     --base-harness examples/base-harness \
     --run-dir runs/live-summary \
     --mode parallel --k 1 --profile research \
     --editor-policy text --max-usd 2
   uv run harness-clone inspect --run-dir runs/live-summary   # includes recorded budget spend
   ```

   Verified live on 21 October 2026 with the text editor: 4 delivered executions and a selected candidate for about $0.08. Expect `inspect` to show `"errors": []` and `"unknown": false`.

7. **Score the result offline** (scores never feed back into refinement). For the summary task the expected artifact is the count and total of `examples/tasks/summary/source/values.json`:

   ```bash
   cat > /tmp/expectations.json <<'EOF'
   {"summary": {"answer.json": {"count": 3, "total": 10}}}
   EOF
   uv run harness-clone evaluate --run-dir runs/live-summary \
     --expectations /tmp/expectations.json \
     --output /tmp/live-results.json
   ```

   Also try `uv run harness-clone resume --run-dir runs/live-summary`: a compatible resume replays no paid work. Changing the task, sources, configuration, or container images must be rejected and requires a new run directory — that rejection leaving the original run resumable is part of the expected behavior.

| Requirement | Implementation / evidence |
|---|---|
| All eight component categories | Bundle validator, compiler, component versioning tests |
| Real Deep Agents tool execution | Compiler tests with a deterministic model and actual filesystem/shell tools |
| Both refinement schedules | Controller count, ancestry, selection, and fallback tests |
| Cross-task experience | Domain/ordinal namespace and two-task stream tests |
| Resume | Interrupted probe replay and immutable publication tests |
| Exact Fireworks model | Provider role-routing and usage tests; live preflight and one complete live run passed |
| Pi Durable | SQLite job identity/reopen tests and TypeScript build; container/live integration pending |
| Credentials and budget | Gateway policy and transactional concurrent reservation tests; Docker isolation tests pass |
| Evaluation separation | External output and evidence allowlist tests |

The architecture/proposal and implementation plan are under `docs/`. Full benchmark replication, performance claims, distributed scheduling, adaptive early stopping, and controller self-modification are outside this MVP.
