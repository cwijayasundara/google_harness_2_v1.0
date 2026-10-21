# Harness Clone

A local research MVP for recursively optimizing a Deep Agents harness with Fireworks DeepSeek V4.1 Flash and Pi Durable code editing.

The optimizer versions system prompts, guardrails, memory, tool guidance, subagents, skills, scripts, middleware, and declared harness settings. The controller, evaluator, credentials, isolation, and spending limits stay outside its editable surface.

## Status

The deterministic workflow, real Deep Agents compilation, persistence, causal experience, Python Pi client, and Pi Durable SQLite reopen behavior are tested locally. Docker execution is verified by the explicitly enabled Docker tests (read-only/non-root/no-public-egress isolation and a real Deep Agents graph executing a candidate script in a container). Live Fireworks preflight (tool round trip, streaming, structured output, usage metadata) passes against the real provider. A complete live experiment run is **not yet verified**: the baseline container execution currently fails with a gateway rejection before budget reservation, and complete Pi code-edit behavior over the real provider is also unverified. The demo uses scripted editors and deterministic Python computations; it demonstrates scheduling and storage, not model quality improvement.

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

## Portfolio agent (recommendations only)

The portfolio commands analyse a frozen daily snapshot of an eToro portfolio and produce research recommendations for the account holder's manual review. **They never place, modify, or cancel orders** — there is no order-placement code path; the only outputs are `recommendations.json` and a human-readable `report.md` per snapshot.

Set both keys in the **host environment only**; the application does not load `.env`, and keys must never enter task sources, candidate bundles, snapshots, or committed files:

```bash
export ETORO_API_KEY=...       # eToro public API (portfolio fetch)
export FIREWORKS_API_KEY=...   # Fireworks (model execution in daily/evolve)
```

Commands (from the repository root; `--base-dir` defaults to `runs/portfolio`):

```bash
# Fetch today's portfolio + market data into a frozen dated snapshot task (no model calls)
uv run harness-clone portfolio fetch --base-dir runs/portfolio
# ...or from a CSV export instead of the live eToro API: --csv /path/to/portfolio.csv

# One execution of the adopted portfolio bundle against today's snapshot (paid; Docker required)
uv run harness-clone portfolio daily --base-dir runs/portfolio --max-usd 2

# Weekly sequential (k=1) evolution over the snapshots in --date's ISO week (paid; Docker required)
uv run harness-clone portfolio evolve --base-dir runs/portfolio --max-usd 5

# Offline scoring of delivered daily recommendations against later prices (no model calls)
uv run harness-clone portfolio score --base-dir runs/portfolio --scoreboard /path/to/scoreboard.json
```

Layout under `--base-dir` (all gitignored):

- `<YYYY-MM-DD>/` — the frozen snapshot task (`task.json`, `source/`), plus the delivered `report.md` (with a host-stamped provenance header: bundle digest, execution id, snapshot fetch time and age) and `recommendations.json` once `daily` runs.
- `runs/daily-<YYYY-MM-DD>/` and `runs/evolve-<YYYY>-W<ww>/` — one fresh run directory per daily run and per ISO-week evolution, so a new snapshot, image, or `--max-usd` never collides with an earlier run and each `--max-usd` caps that invocation only. Re-running the same day or week resumes it; a resumed week replays from the bundle it started with and never replaces a newer adoption.
- `adopted.json` and `bundles/<digest>/` — the adopted bundle digest and a digest-verified archive of every adopted bundle, imported into each new run directory.
- `forward/` — the forward record of delivered daily recommendations; `score` reads it by default (override with `--run-dir`). Evolution re-executions never enter it.

`daily` and `evolve` refuse to run without an explicit nonzero `--max-usd`, a configured `FIREWORKS_API_KEY`, and a running Docker daemon. `daily` executes the currently adopted bundle exactly once and schema-validates its recommendations before recording them; `evolve` runs the Propose–Probe–Compose loop sequentially over the week's snapshots, and a new bundle is adopted only when the final composed execution is delivered and valid — otherwise the previous bundle is kept. Scores are computed offline afterwards and never feed back into evolution directly; only matured outcomes recorded in a prior scoreboard enter later snapshots as `prior_outcomes.json` evidence.

**Privacy:** everything under `runs/` is gitignored. Snapshots contain your holdings, so keep `runs/portfolio` (and any CSV export or scoreboard) out of version control and treat snapshot directories as private financial data.

**Status — read before trusting any output:** live portfolio runs are **unverified and deferred**. No paid `daily`/`evolve` run has been performed, and Task 1's complete live experiment run also remains unverified. The eToro live payload shape is fixture-based and unconfirmed against a real key; equity price fetching uses `yfinance` (declared in `pyproject.toml`, imported lazily), locked in `uv.lock`. The offline loop itself is proven end to end with fake executor/editor doubles in `tests/test_portfolio_integration.py`, including that reflected lessons only flow forward to later dated tasks.

**Operations after setup:** run `portfolio daily` each trading day and `portfolio evolve` + `portfolio score` weekly (scheduling via cron/launchd is your choice). Treat the first **4-week forward soak** (spec milestone 5) as an operational phase, not a code milestone: collect daily recommendations and matured scores for four weeks, review the scoreboard by hand, and only then let the reports inform any manual decision — made by you, outside this tool.

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
   uv run pytest -q            # expect 191 passed, 3 skipped
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
   HARNESS_TEST_DOCKER=1 uv run pytest -m docker -q   # expect 3 passed
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

   Known issue: this currently fails at the baseline execution with a gateway rejection before budget reservation, while step 5 passes. Treat a successful `run` here as the live-verification milestone; it has not been reached yet.

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
| Exact Fireworks model | Provider role-routing and usage tests; live preflight passed, full live run pending |
| Pi Durable | SQLite job identity/reopen tests and TypeScript build; container/live integration pending |
| Credentials and budget | Gateway policy and transactional concurrent reservation tests; real isolation pending |
| Evaluation separation | External output and evidence allowlist tests |

The architecture/proposal and implementation plan are under `docs/`. Full benchmark replication, performance claims, distributed scheduling, adaptive early stopping, and controller self-modification are outside this MVP.
