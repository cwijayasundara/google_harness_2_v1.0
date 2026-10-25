# Self-Evolving Portfolio Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a daily, recommendations-only portfolio agent on the existing harness whose harness bundle evolves weekly against backtested, then forward-scored, recommendation outcomes.

**Architecture:** A trusted host-side layer (CSV portfolio load, market fetch, screening, snapshot building, offline scoring) writes frozen dated tasks and scores matured outcomes; neither it nor any credential ever enters a candidate bundle. The evolvable layer is a new portfolio harness bundle executed daily as-is and evolved weekly through the existing sequential `run_stream` with causal experience.

**Tech Stack:** Python 3.12, pydantic strict records (existing `contracts.py`), httpx, yfinance (new dependency), CoinGecko free API, existing Deep Agents/Docker executor, Fireworks via the existing gateway, pytest + ruff.

**Spec:** [2026-10-21-self-evolving-portfolio-agent-design.md](/Users/chamindawijayasundara/Documents/learning_102/google_harness_2_v1.0/docs/superpowers/specs/2026-10-21-self-evolving-portfolio-agent-design.md)

## Global Constraints

- Recommendations only: no order-placement code path may be introduced anywhere.
- No leverage, derivatives, or shorting recommendations; max single-position target weight 20%; max 5 non-hold calls per day (spec §3.2).
- Portfolio files and market-data credentials live on the host only; never in a task source, bundle, run directory, container, or committed file (spec §4).
- Portfolio snapshots live under `runs/` (already gitignored); never commit portfolio data (spec §4).
- Offline scores never reach online stages; outcomes enter only later-dated tasks, enforced by date ordinal (spec §6, §7.3).
- Controller, evaluator isolation, sandbox, gateway, and budget semantics are unchanged; the only additive core module beside `evaluate.py` is the portfolio scorer (spec §3.3).
- Every paid command requires an explicit nonzero `--max-usd`; daily/evolve refuse a zero cap (spec §4).
- Model remains `accounts/fireworks/models/deepseek-v4p1-flash` for all roles (existing `contracts.MODEL`).
- Existing gates stay green: `uv run ruff check .`, `uv run pytest -q`, `npm test --prefix workers/pi`, `npm run build --prefix workers/pi`.

## Review Focus

- Portfolio payload missing a position field or changing shape: snapshot build aborts with a named error; a partial portfolio is never written (Task 2 test).
- Market data missing or stale for a held ticker: the snapshot records it as missing and the report must say so; no price is ever fabricated (Task 3 test).
- Recommendation naming a ticker outside holdings ∪ candidates: the deterministic validator rejects the artifact (Task 5 test).
- An outcome maturing on or after a task's date appearing in that task: impossible by construction — `outcomes_for` filters `maturity_date < task_date` (Task 6 test).
- Broad-universe fetch partially failing (e.g. S&P 500 list unavailable): `candidates.json` carries `universe_considered`/`universe_returned` counts and the build warns; it never silently presents a reduced universe as complete (Task 4 test).

---

### Task 1: Verify one complete live run (milestone 0 gate)

**Files:**
- Modify (only if a defect is reproduced): the module the reproduction implicates, with its test file.
- Test: existing suite; a new regression test beside the implicated module.

**Interfaces:**
- Consumes: existing CLI (`doctor`, `run`, `inspect`, `evaluate`), Docker images from `containers/compose.yaml`.
- Produces: a recorded verdict — one live run reaching a delivered baseline execution, or a named, reproduced blocker. No portfolio task starts until this passes or the user accepts demo-mode-only.

- [ ] **Step 1: Reproduce the current state**

Run: `uv run harness-clone doctor --live --max-usd 2` (requires the user's spending approval at execution time), then the README manual-checklist step 6 live `run` on `examples/tasks/summary/task.json` with `--editor-policy text`.
Expected: either a delivered execution, or the documented gateway rejection at baseline.

- [ ] **Step 2: If it fails, localize before fixing**

Inspect the gateway rejection path in `src/harness_clone/gateway.py` and the executor path in `src/harness_clone/sandbox.py`; note that commit `fbafafd` (Fireworks base-URL `/v1` fix, already on `main`) may already have resolved it — re-test before writing code.

- [ ] **Step 3: Write a failing regression test for any defect found, fix minimally, re-run**

Run: `uv run pytest -q` — expected: all green including the new regression.

- [ ] **Step 4: Record the verdict**

Append the outcome (live-pass with run directory, or blocker with reproduction) to `IMPLEMENTATION_STATUS.md`.

- [ ] **Step 5: Commit** (only if code changed)

```bash
git add <implicated files> IMPLEMENTATION_STATUS.md
git commit -m "fix: <defect> blocking complete live run"
```

### Task 2: Portfolio contracts and CSV loader

**Files:**
- Modify: `src/harness_clone/contracts.py` (append the records below)
- Create: `src/harness_clone/portfolio_csv.py`
- Test: `tests/test_portfolio_csv.py`
- Fixture: `tests/fixtures/portfolio_pnl.json` (sanitized: invented tickers and amounts)

**Interfaces:**
- Produces: `PortfolioPosition(instrument: str, ticker: str, asset_class: Literal["equity","crypto","etf","cash"], units: float, open_rate: float, current_price: float, unrealized_pnl_pct: float, portfolio_weight: float)`, `PortfolioSnapshot(fetched_at: str, currency: str, cash_weight: float, positions: list[PortfolioPosition])` in `contracts.py`.
- Produces: `normalize_portfolio(payload: dict, fetched_at: str) -> PortfolioSnapshot`; `load_portfolio_csv(path: Path, fetched_at: str) -> PortfolioSnapshot`.

- [ ] **Step 1: Write the fixture**

Save a sanitized holdings payload (invented tickers and amounts) as `tests/fixtures/portfolio_pnl.json`, matching the CSV schema.

- [ ] **Step 2: Write the failing tests**

```python
def test_normalize_computes_weights_and_cash():
    snap = normalize_portfolio(load_fixture(), "2026-10-21T08:00:00Z")
    assert snap.positions[0].portfolio_weight == pytest.approx(0.6, abs=0.01)
    assert snap.cash_weight == pytest.approx(0.4, abs=0.01)

def test_normalize_missing_units_aborts():
    payload = load_fixture(); del payload["positions"][0]["units"]
    with pytest.raises(ValueError, match="units"):
        normalize_portfolio(payload, "2026-10-21T08:00:00Z")

def test_csv_matches_fixture_schema(tmp_path):
    snap = load_portfolio_csv(write_csv(tmp_path), "2026-10-21T08:00:00Z")
    assert snap.model_dump() == normalize_portfolio(load_fixture(), "2026-10-21T08:00:00Z").model_dump()
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_portfolio_csv.py -q` — expected: import/collection failure.

- [ ] **Step 4: Implement**

Add the two records to `contracts.py`; implement normalization (weight = position market value / total account value including cash; missing required field raises `ValueError` naming the field — never defaults); CSV importer with header `instrument,ticker,asset_class,units,open_rate,current_price` producing identical output.

- [ ] **Step 5: Run tests; commit**

Run: `uv run pytest tests/test_portfolio_csv.py -q`
```bash
git add src/harness_clone/contracts.py src/harness_clone/portfolio_csv.py tests/test_portfolio_csv.py tests/fixtures/portfolio_pnl.json
git commit -m "feat: add portfolio CSV loader"
```

### Task 3: Market data providers and pure summary/screening math

**Files:**
- Modify: `pyproject.toml` (add `yfinance>=0.2,<1` to dependencies), `uv.lock` (via `uv lock`)
- Create: `src/harness_clone/portfolio_market.py`
- Test: `tests/test_portfolio_market.py`

**Interfaces:**
- Consumes: `PortfolioSnapshot` (Task 2) for held tickers.
- Produces: `MarketSummary(ticker: str, asset_class: str, last_price: float, return_7d_pct: float, return_30d_pct: float, volatility_30d: float, high_52w_distance_pct: float, missing: bool = False)` and `Candidate(ticker: str, asset_class: str, score: float, metrics: dict[str, float])` in `contracts.py`.
- Produces: `PriceProvider` protocol with `closes(ticker: str, asset_class: str, end: date, days: int) -> list[float]` (oldest→newest); `YFinanceProvider` and `CoinGeckoProvider` implementations; pure functions `market_summary(ticker, asset_class, closes: list[float]) -> MarketSummary` (empty closes → `missing=True`, zeroes elsewhere) and `screen_candidates(summaries: list[MarketSummary], limit: int = 30) -> list[Candidate]` with `score = return_30d_pct - 0.5 * volatility_30d`, missing summaries excluded, ties broken by ticker.
- Produces: `sp500_tickers(fetch: Callable[[str], str]) -> list[str]` parsing the Wikipedia list page's first table with stdlib `html.parser`; `top_crypto_symbols(get: Callable[[str, dict], dict]) -> list[str]` from CoinGecko `/coins/markets?vs_currency=usd&per_page=100`.

- [ ] **Step 1: Write the failing tests** (all provider tests use injected fakes; no network in tests)

```python
def test_summary_from_closes():
    s = market_summary("AAPL", "equity", [100.0] * 29 + [110.0])
    assert s.return_30d_pct == pytest.approx(10.0)
    assert s.missing is False

def test_summary_missing_data_is_flagged_not_fabricated():
    s = market_summary("AAPL", "equity", [])
    assert s.missing is True and s.last_price == 0.0

def test_screen_ranks_and_excludes_missing():
    out = screen_candidates([summary("AAA", 20.0, 4.0), summary("BBB", 5.0, 1.0), missing("CCC")])
    assert [c.ticker for c in out] == ["AAA", "BBB"]
    assert out[0].score == pytest.approx(18.0)
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_portfolio_market.py -q`

- [ ] **Step 3: Implement** providers behind the protocol (yfinance `Ticker.history(end=..., period=...)`; CoinGecko `coins/{id}/market_chart` daily closes), the pure functions exactly as specified, and the two universe fetchers.

- [ ] **Step 4: Run tests; commit**

```bash
git add pyproject.toml uv.lock src/harness_clone/contracts.py src/harness_clone/portfolio_market.py tests/test_portfolio_market.py
git commit -m "feat: add market data providers, summaries, and screener"
```

### Task 4: Snapshot builder and dated task generation

**Files:**
- Create: `src/harness_clone/portfolio_snapshot.py`
- Test: `tests/test_portfolio_snapshot.py`

**Interfaces:**
- Consumes: `PortfolioSnapshot` (Task 2); `MarketSummary`, `Candidate` (Task 3); existing `TaskSpec` and `load_task` in `tasks.py`.
- Produces: `build_snapshot_task(base: Path, day: date, portfolio: PortfolioSnapshot, summaries: dict[str, MarketSummary], candidates: list[Candidate], universe_considered: int, prior_outcomes: list[dict]) -> TaskSpec`. Writes `<base>/<YYYY-MM-DD>/task.json` and `source/{portfolio.json, market.json, candidates.json, prior_outcomes.json}`; `candidates.json` is `{"universe_considered": int, "universe_returned": int, "candidates": [...]}`. Task: `id=f"portfolio-{YYYYMMDD}"`, `domain="portfolio"`, `ordinal=int(YYYYMMDD)`, `output_paths=["recommendations.json", "report.md"]`, prompt instructing the recommendation schema and report structure from the spec §5.2. Any file over 1 MB or total over 2 MB raises `ValueError` (existing task evidence limits); an existing dated directory raises `ValueError` (snapshots are immutable).

- [ ] **Step 1: Write the failing tests**

```python
def test_build_writes_loadable_task(tmp_path, fake_portfolio, fake_summaries):
    task = build_snapshot_task(tmp_path, date(2026, 10, 21), fake_portfolio, fake_summaries, [], 600, [])
    assert task.domain == "portfolio" and task.ordinal == 20261021
    assert load_task(tmp_path / "2026-10-21" / "task.json") == task

def test_build_records_universe_coverage(tmp_path, fake_portfolio, fake_summaries):
    build_snapshot_task(tmp_path, date(2026, 10, 21), fake_portfolio, fake_summaries, [], 600, [])
    data = json.loads((tmp_path / "2026-10-21/source/candidates.json").read_text())
    assert data["universe_considered"] == 600 and data["universe_returned"] == 0

def test_build_refuses_overwrite_and_oversize(tmp_path, fake_portfolio, fake_summaries):
    build_snapshot_task(tmp_path, date(2026, 10, 21), fake_portfolio, fake_summaries, [], 600, [])
    with pytest.raises(ValueError):
        build_snapshot_task(tmp_path, date(2026, 10, 21), fake_portfolio, fake_summaries, [], 600, [])
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_portfolio_snapshot.py -q`

- [ ] **Step 3: Implement** exactly the layout and limits above; serialize with the pydantic models' `model_dump(mode="json")`.

- [ ] **Step 4: Run tests; commit**

```bash
git add src/harness_clone/portfolio_snapshot.py tests/test_portfolio_snapshot.py
git commit -m "feat: build frozen daily portfolio snapshot tasks"
```

### Task 5: Portfolio base harness bundle and recommendation validator

**Files:**
- Create: `examples/portfolio-harness/` mirroring `examples/base-harness/` layout (`manifest.json`, `config.json`, `system.md`, `guardrails.md`, `memory/AGENTS.md`, `tools/descriptions.json`, `subagents/{bull,bear,risk_reviewer}.json`, `skills/{screening,rebalancing,post_mortem}/SKILL.md`, `scripts/portfolio_math.py`, `middleware/audit.py`)
- Create: `src/harness_clone/portfolio_artifacts.py`
- Test: `tests/test_portfolio_artifacts.py`, plus a bundle-validation test reusing `tests/test_bundles.py` helpers

**Interfaces:**
- Consumes: existing `validate_bundle` in `bundles.py`; snapshot JSON shapes (Task 4).
- Produces: `Recommendation(ticker: str, asset_class: str, action: Literal["hold","trim","add","new_idea","exit"], target_weight_pct: float, horizon_days: int, thesis: str, invalidation: str, benchmark: str, evidence_refs: list[str])` in `contracts.py`; `validate_recommendations(text: str, allowed_tickers: set[str]) -> list[Recommendation]` enforcing: tickers ⊆ allowed (holdings ∪ candidates ∪ {SPY, QQQ, BTC, ETH}), `target_weight_pct ≤ 20`, at most 5 non-hold calls, non-hold calls have non-empty `thesis`, `invalidation`, and `evidence_refs`. Violations raise `ValueError` naming the rule.
- `scripts/portfolio_math.py` exposes pure functions `weights(positions: list[dict]) -> dict[str, float]`, `drift(current: dict, target: dict) -> dict[str, float]`, `volatility(closes: list[float]) -> float` for the agent to call in-container; it performs no I/O beyond its arguments.

- [ ] **Step 1: Write the failing validator tests**

```python
def test_validator_rejects_unknown_ticker():
    with pytest.raises(ValueError, match="ticker"):
        validate_recommendations(rec_json(ticker="NOTREAL"), {"AAPL"})

def test_validator_rejects_six_active_calls():
    with pytest.raises(ValueError, match="5"):
        validate_recommendations(rec_json_many(6, action="add"), {"AAPL"})

def test_validator_rejects_overweight_and_bare_calls():
    with pytest.raises(ValueError, match="20"):
        validate_recommendations(rec_json(target_weight_pct=25.0), {"AAPL"})
    with pytest.raises(ValueError, match="invalidation"):
        validate_recommendations(rec_json(invalidation=""), {"AAPL"})
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_portfolio_artifacts.py -q`

- [ ] **Step 3: Author the bundle** per spec §3.2, copying structural conventions from `examples/base-harness/` (same manifest components, `verify`-style skill frontmatter, subagent JSON shape). Guardrails text must state the Global Constraints verbatim limits (20%, 5 calls, recommendations-only, research-only notice).

- [ ] **Step 4: Verify the bundle validates and the scripts compute**

Run: `uv run pytest tests/test_portfolio_artifacts.py tests/test_bundles.py -q` — expected: pass, including a test that `validate_bundle` accepts `examples/portfolio-harness/` and that `portfolio_math.weights` sums to 1.0 on the Task 2 fixture.

- [ ] **Step 5: Commit**

```bash
git add examples/portfolio-harness src/harness_clone/contracts.py src/harness_clone/portfolio_artifacts.py tests/test_portfolio_artifacts.py
git commit -m "feat: add portfolio harness bundle and recommendation validator"
```

### Task 6: Offline scorer, outcomes feedback, and backtest window split

**Files:**
- Create: `src/harness_clone/portfolio_score.py`
- Test: `tests/test_portfolio_score.py`

**Interfaces:**
- Consumes: `Recommendation` (Task 5); a price lookup `PriceFn = Callable[[str, date, date], tuple[float, float] | None]` (start/end price, `None` = unscored).
- Produces: `ScoredCall(task_date: date, maturity_date: date, ticker: str, action: str, benchmark: str, excess_return_pct: float | None, risk_adjusted: float | None, status: Literal["matured","pending","unscored"])`; pure `score_call(rec: Recommendation, task_date: date, today: date, prices: PriceFn, volatility: float) -> ScoredCall` — `maturity_date = task_date + horizon_days`; before maturity → `pending`; missing prices → `unscored`; else excess return vs benchmark minus round-trip cost (0.10% equity/ETF, 0.25% crypto; holds excluded from cost and win-rate), `risk_adjusted = excess / volatility` (volatility 0 → `None`).
- Produces: `scoreboard(run_root: Path, out: Path, today: date, prices: PriceFn, volatilities: dict[str, float]) -> dict` reading delivered `recommendations.json` artifacts from `RunStore` results, writing `{generated_at, matured, pending, unscored, mean_excess_return_pct, hit_rate, mean_risk_adjusted, active_calls}`; refuses `out` inside `run_root` with `ValueError` (same rule as `evaluate.py`); aggregates are `None` below 20 matured active calls, with raw counts always present (spec §7.4).
- Produces: `outcomes_for(scoreboard_path: Path, task_date: date) -> list[dict]` returning only matured calls with `maturity_date < task_date` (the causality filter the snapshot builder consumes).
- Produces: `split_windows(days: list[date], holdout: int = 1) -> tuple[list[date], list[date]]` returning (evolvable, holdout) with the last `holdout` windows held out.

- [ ] **Step 1: Write the failing tests**

```python
def test_unmatured_call_is_pending():
    assert score_call(rec(), date(2026,10,21), date(2026,10,25), fake_prices, 0.2).status == "pending"

def test_matured_call_scores_excess_net_of_cost():
    s = score_call(rec(), date(2026,10,14), date(2026,10,21), fake_prices, 0.2)  # +12% vs +2% SPY, equity
    assert s.status == "matured" and s.excess_return_pct == pytest.approx(9.9)

def test_outcomes_never_leak_into_same_or_earlier_tasks(tmp_path):
    write_scoreboard(tmp_path, maturity=date(2026,10,21))
    assert outcomes_for(tmp_path / "sb.json", date(2026,10,21)) == []
    assert len(outcomes_for(tmp_path / "sb.json", date(2026,10,22))) == 1

def test_scoreboard_refuses_output_inside_run_dir(tmp_path):
    with pytest.raises(ValueError):
        scoreboard(tmp_path, tmp_path / "sb.json", date(2026,10,21), fake_prices, {})

def test_aggregates_suppressed_below_20_matured(tmp_path):
    report = scoreboard_with_matured(tmp_path, 3)
    assert report["matured"] == 3 and report["mean_excess_return_pct"] is None
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_portfolio_score.py -q`

- [ ] **Step 3: Implement** exactly the semantics above; reuse the artifact-reading pattern from `evaluate.py` (`TaskResult` + chosen execution) rather than inventing a second store reader.

- [ ] **Step 4: Run tests; commit**

```bash
git add src/harness_clone/portfolio_score.py tests/test_portfolio_score.py
git commit -m "feat: add offline portfolio scorer with causal outcomes feed"
```

### Task 7: Portfolio operations and CLI (`fetch`, `daily`, `evolve`, `score`)

**Files:**
- Create: `src/harness_clone/portfolio_ops.py`
- Modify: `src/harness_clone/cli.py` (add `portfolio` subcommand group; follow the existing `run` branch's cap/key/Docker guards)
- Test: `tests/test_portfolio_ops.py`, `tests/test_portfolio_cli.py`

**Interfaces:**
- Consumes: Tasks 2–6; existing `DockerExecutor.execute(task, bundle_digest, execution_id)`, `RunStore.publish_bundle/save_execution`, `run_stream` (experience.py), `live_components` and `summarize` (cli.py/demo.py).
- Produces in `portfolio_ops.py` (executor/store injected, so tests use fakes like `tests/fakes.py`):
  - `fetch_task(base: Path, day: date, portfolio_source: PortfolioSnapshot, provider: PriceProvider) -> TaskSpec` — builds summaries for holdings + benchmarks (SPY, QQQ, BTC, ETH), screens the S&P 500 + top-100 crypto universe to 30 candidates, reads prior outcomes via `outcomes_for` if a scoreboard exists, delegates writing to `build_snapshot_task`.
  - `run_daily(store: RunStore, executor, task: TaskSpec, bundle_digest: str) -> ExecutionRecord` — exactly one execution (`execution_id=f"{task.id}/daily"`), validates `recommendations.json` with `validate_recommendations` against snapshot tickers, saves the record; invalid artifact raises `ValueError` and saves nothing as delivered.
  - `run_evolution(tasks: list[TaskSpec], config: ExperimentConfig, store, executor, editor_factory, base_digest: str)` — delegates to `run_stream(..., learn=True)`; callers must pass `config.mode="sequential"`, `k=1` (function raises `ValueError` otherwise).
- CLI: `harness-clone portfolio fetch|daily|evolve|score` with `--base-dir` (default `runs/portfolio`), `--date`, `--csv` (fallback source instead of the API), `--run-dir`, `--max-usd` (required nonzero for `daily`/`evolve`), `--scoreboard` (outside any run dir). `daily` publishes `examples/portfolio-harness/` only on first use and thereafter uses the adopted bundle digest recorded at `<base-dir>/adopted.json`; `evolve` runs the current week's five tasks and updates `adopted.json` only when the composed execution was delivered and schema-valid, else keeps the previous digest (spec §7.4).

- [ ] **Step 1: Write the failing ops tests with fakes**

```python
def test_run_daily_executes_once_and_validates(store, fake_executor):
    record = run_daily(store, fake_executor, fake_task(), "d" * 64)
    assert fake_executor.calls == 1 and record.delivered is True

def test_run_daily_rejects_invalid_recommendations(store, bad_executor):
    with pytest.raises(ValueError):
        run_daily(store, bad_executor, fake_task(), "d" * 64)

def test_evolution_requires_sequential_k1(store):
    with pytest.raises(ValueError, match="sequential"):
        run_evolution([], ExperimentConfig(mode="parallel"), store, None, None, "d" * 64)
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_portfolio_ops.py -q`

- [ ] **Step 3: Implement ops, then CLI wiring** reusing `cli.py`'s existing guards (nonzero cap, `FIREWORKS_API_KEY` present, Docker available, image pinning via `resolve_image_id`) rather than duplicating them.

- [ ] **Step 4: Write the failing CLI tests, then verify**

```python
def test_portfolio_daily_requires_cap(capsys):
    assert main(["portfolio", "daily", "--max-usd", "0"]) == 2
```

Run: `uv run pytest tests/test_portfolio_ops.py tests/test_portfolio_cli.py tests/test_cli.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/harness_clone/portfolio_ops.py src/harness_clone/cli.py tests/test_portfolio_ops.py tests/test_portfolio_cli.py
git commit -m "feat: add portfolio daily/evolve/score CLI operations"
```

### Task 8: Historical bootstrap snapshots (point-in-time backtest input)

**Files:**
- Create: `src/harness_clone/portfolio_history.py`
- Test: `tests/test_portfolio_history.py`

**Interfaces:**
- Consumes: `PriceProvider.closes(..., end=day)` (Task 3 — point-in-time by construction, since `end` bounds the series), `build_snapshot_task` (Task 4), `split_windows` (Task 6).
- Produces: `build_history(base: Path, days: list[date], tickers: list[str], provider: PriceProvider, proxy_portfolio: PortfolioSnapshot) -> list[TaskSpec]` — one snapshot task per day using only closes ending on that day; the portfolio is the current holdings passed in, written into `portfolio.json` with an added `"proxy": true` field so no consumer can mistake it for the historical account (the real historical holdings are unknowable; calls are scored, the proxy portfolio is not — spec §7.2). Summaries whose series ends after `day` raise `ValueError`.

- [ ] **Step 1: Write the failing tests**

```python
def test_history_uses_only_data_up_to_each_day(tmp_path, fake_provider):
    tasks = build_history(tmp_path, [date(2026,9,1)], ["AAPL"], fake_provider, proxy())
    market = json.loads((tasks[0].source_root / "market.json").read_text())
    assert market["AAPL"]["last_price"] == fake_provider.price_on("AAPL", date(2026,9,1))

def test_history_marks_portfolio_as_proxy(tmp_path, fake_provider):
    tasks = build_history(tmp_path, [date(2026,9,1)], ["AAPL"], fake_provider, proxy())
    assert json.loads((tasks[0].source_root / "portfolio.json").read_text())["proxy"] is True
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_portfolio_history.py -q`

- [ ] **Step 3: Implement; Step 4: run tests; commit**

```bash
git add src/harness_clone/portfolio_history.py tests/test_portfolio_history.py
git commit -m "feat: build point-in-time historical bootstrap snapshots"
```

### Task 9: End-to-end offline proof, Docker isolation test, and documentation

**Files:**
- Create: `tests/test_portfolio_integration.py` (offline: fake executor/editor through `run_daily` + one sequential `run_evolution` over three dated tasks, asserting lessons only flow forward)
- Modify: `tests/test_sandbox.py` (add one `docker`-marked test executing the portfolio bundle's `portfolio_math.py` in-container, following the existing pattern)
- Modify: `README.md` (portfolio section), `IMPLEMENTATION_STATUS.md`

**Interfaces:**
- Consumes: all previous tasks. Produces no new public API.

- [ ] **Step 1: Write the integration test**

```python
def test_forward_lessons_only_flow_forward(tmp_path):
    results = run_three_day_evolution(tmp_path)  # fake executor/editor
    day3_evidence = evidence_task_ids(results[2])
    assert "portfolio-day1" in day3_evidence and "portfolio-day3" not in day3_evidence
```

- [ ] **Step 2: Run the full verification set**

Run: `uv run ruff check . && uv run pytest -q && npm test --prefix workers/pi && npm run build --prefix workers/pi`
Expected: all green (baseline before this plan: 100 passed, 2 skipped).

- [ ] **Step 3: Run the Docker isolation test**

Run: `HARNESS_TEST_DOCKER=1 uv run pytest -m docker -q` — expected: existing tests plus the portfolio script test pass.

- [ ] **Step 4: Document operations in `README.md`**

Cover: key setup (`FIREWORKS_API_KEY` in `.env`), `portfolio fetch/daily/evolve/score` examples with explicit `--max-usd` and `--csv` for fetch, the recommendations-only boundary, snapshot privacy (`runs/` gitignored), the 4-week forward soak (spec milestone 5) as an operational phase — not code — and the unverified-until-run status of live portfolio runs.

- [ ] **Step 5: Commit**

```bash
git add tests/test_portfolio_integration.py tests/test_sandbox.py README.md IMPLEMENTATION_STATUS.md
git commit -m "test: prove portfolio evolution loop end to end offline"
```

## Execution order and gates

Tasks run in order 1→9. Task 1 is a hard gate for any live use; Tasks 2–6 are pure/offline and may proceed even if Task 1 is blocked, but nothing may be scheduled live until it passes. Tasks 7–9 depend on all earlier interfaces. After Task 9, operation begins: daily `portfolio daily`, weekly `portfolio evolve` + `portfolio score`, and the spec's 4-week forward soak before the reports inform any manual decision.
