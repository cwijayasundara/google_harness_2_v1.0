# Harness Clone

A local research MVP for recursively optimizing a Deep Agents harness with Fireworks DeepSeek V4.1 Flash and Pi Durable code editing.

The optimizer versions system prompts, guardrails, memory, tool guidance, subagents, skills, scripts, middleware, and declared harness settings. The controller, evaluator, credentials, isolation, and spending limits stay outside its editable surface.

## Status

The deterministic workflow, real Deep Agents compilation, persistence, causal experience, Python Pi client, and Pi Durable SQLite reopen behavior are tested locally. Docker execution and live Fireworks/Pi compatibility are **not yet verified**. The demo uses scripted editors and deterministic Python computations; it demonstrates scheduling and storage, not model quality improvement.

## Install and try the offline demo

Use Python 3.12, uv, Node >=22.19.0, and Docker for live workers. Run from this repository's implementation worktree:

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
uv run harness-clone doctor --live --max-usd 1
uv run harness-clone run \
  --task examples/tasks/summary/task.json \
  --base-harness examples/base-harness \
  --run-dir runs/live-summary \
  --mode parallel --k 1 --profile research \
  --editor-policy pi --max-usd 2
```

The model is fixed to `accounts/fireworks/models/deepseek-v4p1-flash`. Every execution/editor/subagent/summary role uses it. The gateway meters calls against a shared SQLite ledger and stops further paid work if usage is unknown or the configured cap is exhausted. Published token prices are embedded as estimates; verify current rates before using the cost cap as a financial control. A reservation conservatively estimates the next request; actual provider billing may exceed estimates and is recorded rather than discarded.

Live doctor checks cover a tool round trip, structured JSON, streaming, and usage metadata. Executor compaction/restart and complete Pi code-edit behavior require separate container-backed validation; doctor never declares those verified.

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

| Requirement | Implementation / evidence |
|---|---|
| All eight component categories | Bundle validator, compiler, component versioning tests |
| Real Deep Agents tool execution | Compiler tests with a deterministic model and actual filesystem/shell tools |
| Both refinement schedules | Controller count, ancestry, selection, and fallback tests |
| Cross-task experience | Domain/ordinal namespace and two-task stream tests |
| Resume | Interrupted probe replay and immutable publication tests |
| Exact Fireworks model | Provider role-routing and usage tests; live compatibility pending |
| Pi Durable | SQLite job identity/reopen tests and TypeScript build; container/live integration pending |
| Credentials and budget | Gateway policy and transactional concurrent reservation tests; real isolation pending |
| Evaluation separation | External output and evidence allowlist tests |

The architecture/proposal and implementation plan are under `docs/`. Full benchmark replication, performance claims, distributed scheduling, adaptive early stopping, and controller self-modification are outside this MVP.
