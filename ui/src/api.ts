// Typed client for the local cockpit API (src/harness_clone/ui_server.py).

export type AssetClass = "equity" | "crypto" | "etf" | "cash";
export type Action = "hold" | "trim" | "add" | "new_idea" | "exit";

export interface Position {
  instrument: string;
  ticker: string;
  asset_class: AssetClass;
  units: number;
  open_rate: number;
  current_price: number;
  unrealized_pnl_pct: number;
  portfolio_weight: number;
}

export interface Portfolio {
  fetched_at: string;
  currency: string;
  cash_weight: number;
  positions: Position[];
  proxy?: boolean;
}

export interface MarketSummary {
  ticker: string;
  asset_class: string;
  last_price: number;
  return_7d_pct: number;
  return_30d_pct: number;
  volatility_30d: number;
  high_52w_distance_pct: number;
  missing?: boolean;
}

export interface Candidate {
  ticker: string;
  asset_class: string;
  score: number;
  metrics: Record<string, number>;
}

export interface Candidates {
  universe_considered?: number;
  universe_returned?: number;
  universe_missing?: Record<string, unknown>;
  candidates?: Candidate[];
}

export interface Recommendation {
  ticker: string;
  asset_class: string;
  action: Action;
  target_weight_pct: number;
  horizon_days: number;
  thesis: string;
  invalidation: string;
  benchmark: string;
  evidence_refs: string[];
}

export interface SnapshotSummary {
  date: string;
  week: string;
  holdings: number;
  proxy: boolean;
  has_recommendations: boolean;
  non_hold_calls: number;
}

export interface Snapshot {
  date: string;
  portfolio: Portfolio | null;
  market: Record<string, MarketSummary>;
  candidates: Candidates;
  prior_outcomes: unknown[];
  recommendations: Recommendation[] | null;
  report: string | null;
}

export interface ScoredCall {
  task_date: string;
  maturity_date: string;
  ticker: string;
  action: Action;
  benchmark: string;
  excess_return_pct: number | null;
  risk_adjusted: number | null;
  status: "matured" | "pending" | "unscored";
  reason?: string | null;
}

export interface Scoreboard {
  generated_at: string;
  matured: number;
  pending: number;
  unscored: number;
  skipped: number;
  mean_excess_return_pct: number | null;
  hit_rate: number | null;
  mean_risk_adjusted: number | null;
  active_calls: number;
  calls: ScoredCall[];
}

export interface EvolutionWeek {
  week: string;
  executions: number;
  delivered: number;
  base: string | null;
}

export interface Evolution {
  adopted_bundle: string | null;
  archived_bundles: string[];
  weeks: EvolutionWeek[];
}

export interface Status {
  base_dir: string;
  demo: boolean;
  adopted_bundle: string | null;
  docker: boolean;
  keys: { fireworks: boolean };
  max_ui_usd: string;
  job_running: string | null;
}

export type Command = "fetch" | "daily" | "evolve" | "score";

export interface JobRequest {
  command: Command;
  date?: string;
  max_usd?: string;
  confirm_paid?: boolean;
  csv?: string;
}

export interface Job {
  id: string;
  command: Command;
  argv: string[];
  status: "running" | "succeeded" | "failed";
  returncode: number | null;
  started_at: string;
  finished_at: string | null;
  log_tail: string;
  result: Record<string, unknown> | null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep the status text */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  status: () => request<Status>("/api/status"),
  snapshots: () => request<SnapshotSummary[]>("/api/snapshots"),
  snapshot: (day: string) => request<Snapshot>(`/api/snapshots/${day}`),
  scoreboard: () => request<Scoreboard | null>("/api/scoreboard"),
  evolution: () => request<Evolution>("/api/evolution"),
  jobs: () => request<Job[]>("/api/jobs"),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  startJob: (body: JobRequest) =>
    request<Job>("/api/jobs", { method: "POST", body: JSON.stringify(body) }),
};

/** Resolve an evidence ref such as `market.json:AAPL.return_30d_pct` against a snapshot. */
export function resolveEvidence(
  ref: string,
  snapshot: Snapshot,
): { label: string; value: string } | null {
  const match = /^([a-z_]+)\.json:([^.]+)\.?(.*)$/i.exec(ref.trim());
  if (!match) return null;
  const [, file, ticker, field] = match;
  let record: Record<string, unknown> | undefined;
  if (file === "market") record = snapshot.market[ticker] as unknown as Record<string, unknown>;
  if (file === "portfolio")
    record = snapshot.portfolio?.positions.find((p) => p.ticker === ticker) as unknown as
      | Record<string, unknown>
      | undefined;
  if (file === "candidates") {
    const c = snapshot.candidates.candidates?.find((x) => x.ticker === ticker);
    record = c ? { ...c.metrics, score: c.score } : undefined;
  }
  if (!record) return null;
  const key = pickField(record, field);
  if (!key) return null;
  return { label: `${ticker} · ${key}`, value: formatEvidence(key, record[key]) };
}

function pickField(record: Record<string, unknown>, field: string): string | null {
  if (!field) return null;
  if (field in record) return field;
  // Agents sometimes cite `30d_return` for `return_30d_pct`; match on shared tokens.
  const tokens = field.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  const scored = Object.keys(record)
    .map((k) => ({ k, hits: tokens.filter((t) => k.toLowerCase().includes(t)).length }))
    .filter((x) => x.hits > 0)
    .sort((a, b) => b.hits - a.hits);
  return scored[0]?.k ?? null;
}

function formatEvidence(key: string, value: unknown): string {
  if (typeof value !== "number") return String(value);
  if (key.endsWith("_pct") && !key.startsWith("unrealized")) return `${value.toFixed(2)}%`;
  if (key === "portfolio_weight" || key === "unrealized_pnl_pct" || key === "cash_weight")
    return `${(value * 100).toFixed(1)}%`;
  if (key.startsWith("volatility")) return `${(value * 100).toFixed(1)}% ann.`;
  return Math.abs(value) >= 100 ? value.toLocaleString() : value.toFixed(3);
}

export const pct = (v: number, digits = 1) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(digits)}%`;
