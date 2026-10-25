# Self-Evolving Portfolio Agent — Design

Date: 2026-10-21
Status: Approved design, awaiting written-spec review
Classification: Architectural (new subsystem on the existing harness-clone)

## 1. Purpose and outcome

Build a recommendations-only agent on top of this repository's existing
self-evolving harness (`src/harness_clone/`) that, every day:

1. Reads the user's portfolio from a holdings CSV and a broad equity/crypto market scan.
2. Produces a dated recommendation report (hold / trim / add / new idea).
3. Improves over time by evolving its own harness — prompts, guardrails,
   memory, skills, scripts, subagents, and settings — through the harness's
   existing Propose–Probe–Compose loop, scored by realized recommendation
   outcomes.

Success is defined by the offline scoreboard (Section 7): forward-scored
recommendations beating asset-appropriate benchmarks on a risk- and
cost-adjusted basis over rolling windows. A harness variant is only an
improvement if the scoreboard says so on data the variant did not see.

### User decisions (settled 2026-10-21)

| Decision | Choice |
|---|---|
| Portfolio source | Holdings CSV, host-side only |
| Success measurement | Backtest bootstrap, then forward paper-tracking |
| Cadence / horizon | Daily review, 1-week scoring horizon |
| Discovery universe | S&P 500 + top-100 crypto (host-screened) |
| Execution boundary | Recommendations only; never trades |

### Explicit non-goals

- No trade execution, order placement, or broker write access of any kind.
- No leverage, derivatives, or shorting recommendations.
- No real-time/intraday trading signals; the cadence is daily.
- No personalized financial advice claims; every report carries a
  research-only, not-financial-advice notice.
- No changes to the harness controller, evaluator isolation rules, sandbox,
  or budget ledger semantics (Section 4).

## 2. Why this harness fits

The repository already implements the self-evolution engine this design
needs, so the build reuses it rather than replacing it:

- Eight editable component categories (system, guardrails, memory, tools,
  subagents, skills, scripts, middleware) plus declared settings
  (`context_tokens`, `compaction_fraction`, `retrieval_limit`,
  `delegation_depth`), versioned as SHA-256 bundles with patch validation.
- Propose–Probe–Compose refinement with INV/DOM proposals, probe and
  composed executions, bounded repair, and `KEEP_BASE` fallback.
- Sequential mode carries the adopted bundle forward across an ordered task
  stream; parallel mode searches from shared baseline evidence.
- Causal experience: per-domain, ordinal-scoped `ExperienceLibrary`
  snapshots expose only lessons from earlier ordinals, each tied to an
  adopted bundle digest and evidence references.
- Offline evaluation is structurally separated: expectations and scores
  live outside the online run directory and never reach online stages.
- Isolation and spend controls: Docker execution with no public egress,
  gateway-only model access, host-held credentials, and a SQLite budget
  ledger that blocks paid work past an explicit `--max-usd` cap.

A daily portfolio review maps directly onto a task stream:
`domain = "portfolio"`, `ordinal = YYYYMMDD`, one task per trading day.

## 3. Architecture

Two layers. The trust boundary is the design's central rule: everything the
evolving agent can edit is in the inner layer; everything that fetches
data, scores outcomes, or controls spend is in the outer layer and is never
part of a candidate bundle. This is what prevents the agent from improving
its score by editing its own grader, data, or budget.

### 3.1 Trusted outer layer (new code, outside the editable surface)

Located outside any harness bundle (proposed: `src/harness_clone/portfolio/`
for library code plus a thin CLI entry, final layout set in the plan):

- **Portfolio CSV loader** — reads a holdings file into the snapshot schema.
  The file stays on the host. It is never written into a task source,
  bundle, run directory, or container. The test fixture uses the same
  schema.
- **Market data fetcher** — equities via yfinance, crypto via CoinGecko
  (free tiers first; provider behind a small interface so Alpaca/TwelveData
  can replace yfinance without touching downstream code). Fetches daily
  bars and basic fundamentals for: current holdings, the S&P 500 index
  constituents, and the top-100 crypto assets by market cap.
- **Screener** — reduces the broad universe to at most 30 ranked candidate
  summaries (momentum, volatility, volume, distance from highs/lows) plus
  index/benchmark context. This reduction is mandatory, not an
  optimization: task source evidence is capped at 1 MB per artifact and
  2 MB total, so the full universe can never enter a task raw.
- **Snapshot builder** — writes one frozen, dated task source directory
  (Section 5) and the matching `TaskSpec`. Snapshots are immutable once
  written; the harness fingerprint rejects a changed stream, which is the
  desired reproducibility guarantee.
- **Outcome scorer** — offline only (Section 7). Reads matured
  recommendations, fetches realized prices, writes a scoreboard file
  outside the run directory and an outcomes file that the snapshot builder
  may include in *later-dated* tasks as evidence.
- **Scheduler entry point** — a single command (`portfolio daily`) that
  runs fetch → snapshot → harness execution → report, and a second
  (`portfolio evolve`) that runs the weekly evolution and scoring pass.
  Cron/launchd wiring is the user's choice and out of scope for the code.

### 3.2 Evolvable inner layer (a new base harness bundle)

Proposed `examples/portfolio-harness/` following the existing base-harness
layout:

- `system.md` — analyst role: produce the recommendation schema exactly,
  cite snapshot figures for every claim, state an invalidation condition
  for every non-hold call.
- `guardrails.md` — hard behavioral rules: recommendations only; no
  leverage/derivatives/shorts; maximum single-position target weight 20%;
  maximum 5 non-hold calls per day; every new idea must name its benchmark;
  research-only notice required in the report.
- `subagents/` — `bull.json`, `bear.json`, `risk_reviewer.json` for
  adversarial review of proposed changes before final selection.
- `skills/` — `screening` (how to read `candidates.json`), `rebalancing`
  (drift and target-weight method), `post_mortem` (how to analyze matured
  outcomes in `prior_outcomes.json`).
- `scripts/` — deterministic Python only: portfolio weights, drift,
  volatility, drawdown, and benchmark-relative math. The model proposes;
  these scripts compute. No network access (the sandbox has none).
- `memory/` — seeded with the user's stated constraints; thereafter carries
  evolved lessons via the experience mechanism.
- `tools/descriptions.json`, `middleware/`, `config.json` — per the
  existing bundle contract.

### 3.3 What does not change

`controller.py`, `evaluate.py` isolation rules, `sandbox.py`, `gateway.py`,
budget accounting, bundle validation, and the experience causality rules
are used as-is. The one additive core change anticipated is a new offline
scorer module beside `evaluate.py`; it must preserve the existing rule
that evaluation output is refused inside the online run directory.

## 4. Trust, safety, and compliance boundaries

- Execution containers keep the existing posture: non-root, dropped
  capabilities, read-only bundle/source mounts, no public egress, scoped
  gateway token. Portfolio files and market-data credentials never enter a container.
- Portfolio snapshots are private financial data: snapshot directories live
  outside git (gitignored run area, e.g. `runs/portfolio/`), never in
  `examples/` or any committed path.
- Recommendations are research output for the account holder's manual
  review. The system has no order-placement code path at all.
- Paid model calls remain gated by `--max-usd` with the existing ledger;
  daily and evolution commands refuse to run with a zero cap.
- Failure of the holdings CSV or a market provider aborts snapshot building
  with a clear error. The system never fabricates a partial portfolio or
  silently substitutes stale prices; a stale snapshot is labeled with its
  fetch timestamp and age in the report.

## 5. Data contracts

### 5.1 Daily task snapshot (`source_root/`, all JSON, ≤1 MB each)

- `portfolio.json` — fetch timestamp, account currency, cash weight, and
  positions: `{instrument, ticker, asset_class, units, open_rate,
  current_price, unrealized_pnl_pct, portfolio_weight}`.
- `market.json` — per holding and benchmark (SPY, QQQ, BTC, ETH): last 60
  daily closes summary (last price, 7d/30d return, 30d volatility,
  52w position), plus index levels. Raw bars are summarized, not dumped.
- `candidates.json` — screener output: at most 30 entries from the
  S&P 500 + top-100 crypto universe with the screening metrics and the
  screen's ranking rationale fields.
- `prior_outcomes.json` — matured past recommendations with realized
  benchmark-relative results (Section 7.3). Empty on day one. This is
  evidence for reflection, never a score feed into the same task.

### 5.2 Recommendation artifact (`recommendations.json`, the task output)

A list of records, one per call:

```json
{
  "ticker": "AAPL", "asset_class": "equity",
  "action": "hold|trim|add|new_idea|exit",
  "target_weight_pct": 8.0,
  "horizon_days": 7,
  "thesis": "…", "invalidation": "…",
  "benchmark": "SPY",
  "evidence_refs": ["market.json:AAPL.30d_return"]
}
```

Plus `report.md`: a human-readable summary leading with portfolio-level
risk, then per-call rationale, the research-only notice, snapshot age, and
the bundle digest that produced it (provenance).

Schema validation is deterministic (controller-side, using the existing
artifact contract checks). Malformed output fails the execution; it is
never hand-repaired.

## 6. Evolution loop

Two tempos, deliberately. Running the full Propose–Probe–Compose loop
daily would multiply model spend and evolve the harness against
single-day noise; never evolving would waste the harness.

- **Daily (execution only):** one execution of the currently adopted
  bundle on that day's snapshot task, producing the report. No proposals,
  no bundle changes.
- **Weekly (evolution):** one sequential-mode `run_stream` pass over the
  week's five daily tasks with `learn=True`. Sequential mode carries the
  composed bundle forward task to task; the experience library appends
  evidence-supported lessons scoped by date ordinal, so a lesson can only
  influence later days. The adopted end-of-week bundle becomes the daily
  bundle for the following week. Bundle digests are recorded so any day's
  report traces to the exact harness that produced it.
- **Bootstrap (once, before live days):** historical snapshots are rebuilt
  point-in-time (only data available on that date) for a past window and
  run through the same weekly evolution to warm up memory and skills
  before real money-adjacent recommendations begin. A final holdout slice
  of the historical window is scored but never evolved against
  (Section 7.2).

Selection stays as the harness defines it: the editor chooses among
delivered executions; offline scores are never shown to online stages.

## 7. Scoring

### 7.1 Forward scoring (the real scoreboard)

Each recommendation matures at `horizon_days` (7). The scorer then
computes, per call and in aggregate over rolling 4-week windows:

- Excess return vs its named benchmark over the horizon.
- Hit rate (sign of excess return, holds excluded from the win rate but
  tracked for drift).
- Risk adjustment: excess return divided by realized volatility over the
  horizon.
- Cost adjustment: a fixed assumed round-trip cost (10 bps equities,
  25 bps crypto) deducted from active calls.
- Turnover: number and weight of non-hold calls (penalizes churn).

### 7.2 Backtest scoring (bootstrap only)

Walk-forward on point-in-time snapshots: evolve on window *N*, score on
window *N+1*, never the reverse. The last window is holdout — scored,
reported, and excluded from all evolution and lesson generation. Backtest
results are labeled as simulated everywhere they appear; they bootstrap
the harness but do not count as evidence of live quality.

### 7.3 Feedback path

Scored outcomes are written to the scoreboard (outside the run directory,
per the existing evaluator rule) and, in summarized form, into the next
snapshots' `prior_outcomes.json`. That file is the only channel by which
results influence the agent, and causality is enforced by date ordinal:
an outcome can only appear in tasks dated after its maturity date.

### 7.4 Anti-overfitting rules

- Minimum 20 matured active calls before any aggregate claim is reported
  as meaningful; smaller samples are displayed as raw counts only.
- A bundle is adopted for daily use only if its weekly evolution completed
  with a delivered, schema-valid composed execution; `KEEP_BASE` and
  fallback outcomes keep the previous bundle.
- Holdout and forward scoreboards are reported separately, never blended.

## 8. Operations

- Commands (exact names set in the plan): a `portfolio` CLI group or a
  small wrapper script exposing `doctor` (reuse existing), `daily`,
  `evolve`, `score`, and `inspect` (reuse existing) flows.
- Expected cost drivers: one execution/day at daily tempo; weekly
  evolution at `k=1` sequential over five tasks. The demo's `k=2`
  accounting (eight executions and six editing stages per task) is the
  only measured reference point, so the plan must measure actual `k=1`
  stage counts and set a `--max-usd` cap per run before any live
  scheduling.
- All runs remain resumable and replay-free under the existing store;
  changing a snapshot, task, or image requires a new run directory, which
  the daily layout gets naturally from its date.

## 9. Milestones

0. **Live-run verification.** Resolve the currently documented blocker —
   a complete live `run` fails with a gateway rejection at baseline while
   `doctor --live` passes — and prove one end-to-end live execution.
   Nothing portfolio-specific is built on an unverified live path.
1. **Trusted data layer.** CSV portfolio loader, market fetcher,
   screener, and snapshot builder, with deterministic offline tests using
   recorded fixtures.
2. **Portfolio base harness + single task.** Bundle per Section 3.2,
   recommendation schema validation, one offline/demo-mode run producing
   a schema-valid `recommendations.json` and `report.md`.
3. **Scorer + backtest bootstrap.** Offline scorer, point-in-time
   historical snapshots, walk-forward evolution, holdout report.
4. **Forward loop.** Daily execution command, weekly evolution command,
   scoreboard reporting, provenance from report to bundle digest.
5. **Soak and review.** At least four weeks of forward paper-tracking
   reviewed against Section 7.4 thresholds before the user relies on the
   reports for manual decisions.

## 10. Risks and open questions

- **Live harness run is unverified** (gateway rejection). Milestone 0
  gates everything; if it cannot be resolved, the design falls back to
  demo-mode scheduling proof only, and that limitation is reported rather
  than worked around.
- **Free market-data tiers** (yfinance is unofficial; CoinGecko free is
  rate-limited) may throttle the broad daily screen. Mitigation: provider
  interface, cached daily bars, screener tolerant of partial universe
  coverage as long as missing data is reported, never invented.
- **Daily noise vs weekly evolution** may still overfit short samples.
  Mitigation: Section 7.4 minimum-sample and rolling-window rules.
- **Model quality** for financial reasoning is unproven on this stack;
  the deterministic scripts and adversarial subagents exist to bound, not
  eliminate, that risk. Reports remain human-reviewed, always.
- **Regulatory posture:** personal research tooling for one account
  holder, no advice distribution, no execution. If use ever extends
  beyond that, this design must be revisited.

## 11. Testing strategy

- Deterministic offline tests for every trusted-layer module (fixture
  snapshots, screener ranking, schema validation, scorer math on known
  price series) in the existing pytest style.
- Existing suite must stay green: `uv run pytest -q`, `uv run ruff check .`,
  plus Pi worker tests untouched.
- Contract tests that no candidate bundle can contain scorer, fetcher,
  credential, or budget code (editable-surface validation already rejects
  out-of-surface paths; tests assert the portfolio modules are outside it).
- One Docker-isolated execution test reusing the existing `docker` marker
  pattern before any live run.
