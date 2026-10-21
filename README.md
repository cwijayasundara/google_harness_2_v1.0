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
uv run pytest -q                       # expect 261 passed, 3 skipped
uv run harness-clone doctor            # offline environment report
uv run harness-clone demo --run-dir runs/demo-parallel --mode parallel --k 2
uv run harness-clone inspect --run-dir runs/demo-parallel
```

**3. Build the container images and check isolation** (Docker running; still free). Rebuild after any source change. Live runs pin image IDs, so a rebuilt image needs a fresh `--run-dir`:

```bash
docker compose -f containers/compose.yaml --profile build build
HARNESS_TEST_DOCKER=1 uv run pytest -m docker -q     # expect 3 passed
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

**7. Run the portfolio agent** (recommendations only; see [Portfolio agent](#portfolio-agent-recommendations-only) for the full layout and safety notes). With no eToro key, start from a CSV export using this exact header. A row with `asset_class` `cash` is your cash balance (`units × current_price`):

```csv
instrument,ticker,asset_class,units,open_rate,current_price
Apple Inc,AAPL,equity,10,150.00,190.00
Bitcoin,BTC,crypto,0.05,40000,65000
Cash,CASH,cash,500,1,1
```

```bash
# Each trading day: freeze today's snapshot (free), then run the adopted bundle once (paid)
uv run harness-clone portfolio fetch --csv ~/portfolio.csv          # or omit --csv to use your eToro keys
uv run harness-clone portfolio daily --max-usd 2                    # writes runs/portfolio/<date>/report.md and recommendations.json

# Once a week, after the week's dailies: evolve the bundle (paid), then score matured calls (free)
uv run harness-clone portfolio evolve --max-usd 5
uv run harness-clone portfolio score --scoreboard ~/portfolio-scoreboard.json
```

All portfolio commands default to `--base-dir runs/portfolio` and today's date; pass `--date YYYY-MM-DD` to act on another day (for `evolve`, that day's ISO week). `daily` fetches the snapshot itself if `fetch` has not run yet. Pass `--scoreboard` to `fetch`/`daily` to feed matured outcomes from an earlier scoreboard into the new snapshot. Keep the scoreboard file outside `runs/portfolio`; `score` refuses to write it inside the run directory it reads. Run `evolve` once per week, at week end. Running it again in the same week after a new snapshot, or with a different `--max-usd`, is refused; pass a new `--run-dir` if you need to.

**Troubleshooting**

- `no baseline execution delivered`: the run reached Docker but the baseline failed. Read the execution records with `sqlite3 <run-dir>/run.sqlite "select id, value from records where kind='execution'"`; `worker-exit` with zero tokens means the gateway refused the request before any paid call.
- `stream inputs changed` or `incompatible experiment configuration`: the task, sources, images, or `--max-usd` differ from what that run directory started with. Use a new `--run-dir`.
- `live execution requires a key and nonzero spending cap`: export `FIREWORKS_API_KEY` and pass a nonzero `--max-usd`.
- Images rebuilt mid-experiment: start a fresh `--run-dir`; resume rejects changed image IDs by design.

## Portfolio Cockpit (UI)

A local web UI for the portfolio agent. It is research-only like the CLI: nothing in it can place, modify, or cancel orders.

```bash
scripts/setup.sh --demo        # infra: Python env, Node packages, Docker images, plus synthetic demo data
scripts/start-backend.sh --demo   # API on http://127.0.0.1:8765, serving runs/portfolio-demo
scripts/start-frontend.sh      # UI on http://127.0.0.1:5173
```

Drop `--demo` from `start-backend.sh` to work on your real `runs/portfolio`. `setup.sh --no-docker` skips the image build if you only want to browse. The backend reads `FIREWORKS_API_KEY`, `ETORO_API_KEY` and `ETORO_USER_KEY` from the shell you start it in, and the UI shows only whether each key is set.

What's on screen:

- **Time machine**: every frozen snapshot, grouped by ISO week, with a tick per active call and a flag on weeks that ran evolution. Use ← / → to scrub.
- **Portfolio map**: a treemap sized by weight and coloured by unrealised P&L. **Now ⇄ Proposed** morphs it into the agent's target weights, so trims shrink, exits vanish, new ideas appear dashed, and cash absorbs the rest. **Table** shows the same numbers as rows.
- **Agent calls**: one card per active call with current → target weight, thesis, invalidation and benchmark. Hover an evidence ref (for example `market:AAPL.return_30d_pct`) to see the snapshot value it cites. Hovering a card highlights that holding on the map.
- **Report / Market & candidates / Scoreboard / Evolution**: the delivered `report.md` (sanitised before rendering), the market summaries and screened candidates with coverage counts, the hit rate and excess return of matured calls, and the weekly bundle lineage.
- **Mission control** (bottom right): runs `portfolio fetch` (paste or upload a CSV, or use the eToro API), `daily`, `evolve` and `score` as background jobs with a live log. Paid commands need a typed spending cap (at most $25 from the UI) and a confirmation tick, and only one job runs at a time.

Safety rails: the API binds only to a loopback address and refuses any other `--host`. Demo directories (marked with `.demo`) are read-only, so the API refuses every job on them. The seeder never replaces a directory without that marker.

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

Set keys in the **host environment only**; the application does not load `.env`, and keys must never enter task sources, candidate bundles, snapshots, or committed files:

```bash
export FIREWORKS_API_KEY=...   # Fireworks (model execution in daily/evolve)
export ETORO_API_KEY=...       # eToro Public API application key  } only needed to fetch
export ETORO_USER_KEY=...      # eToro Public API user key         } without --csv
export ETORO_ENVIRONMENT=real  # optional: real (default) or demo
```

**Getting eToro keys.** Sign in at [api-portal.etoro.com](https://api-portal.etoro.com), open *Settings → Trading → API Key Management*, and create a key with **Read** permission for the environment you want (Real or Demo; each key works for one). You get two values: the public **API key** (`ETORO_API_KEY`) and the **user key** (`ETORO_USER_KEY`), which is shown only once. A read-only key is enough, since the agent never trades.

How the fetch works: it reads `GET /api/v1/trading/info/{real|demo}/pnl` for holdings and cash, then `GET /api/v1/market-data/instruments` to turn instrument ids into tickers and asset classes. Every request carries both keys plus a fresh `x-request-id`. Lots of one instrument are merged, and copy-trading positions and the cash reserved inside a copy count as holdings. Stocks, ETFs and crypto are supported. The fetch aborts with a named error, rather than writing a partial portfolio, if the account holds a short or leveraged position or another instrument type (forex, commodities, indices, CFDs), or if the account currency isn't USD. In that case export the supported holdings and use `--csv`.

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

**Status — read before trusting any output:** live portfolio runs are **unverified**. The underlying live harness run is verified (21 October 2026), but no paid `daily`/`evolve` run has been performed yet. The eToro client follows the published Public API spec (auth headers, PnL and instruments schemas), but no fetch with real keys has been run yet; equity price fetching uses `yfinance` (declared in `pyproject.toml`, imported lazily), locked in `uv.lock`. The offline loop itself is proven end to end with fake executor/editor doubles in `tests/test_portfolio_integration.py`, including that reflected lessons only flow forward to later dated tasks.

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
   uv run pytest -q            # expect 261 passed, 3 skipped
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
