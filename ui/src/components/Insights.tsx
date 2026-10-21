import DOMPurify from "dompurify";
import { marked } from "marked";
import { useMemo, useState } from "react";
import { pct, type Evolution, type ScoredCall, type Scoreboard, type Snapshot } from "../api";
import { ACTION_LABEL } from "./PortfolioMap";

type Tab = "market" | "report" | "score" | "evolution";

export function Insights({
  snapshot,
  scoreboard,
  evolution,
}: {
  snapshot: Snapshot;
  scoreboard: Scoreboard | null;
  evolution: Evolution | null;
}) {
  const [tab, setTab] = useState<Tab>("report");
  const tabs: [Tab, string][] = [
    ["report", "Report"],
    ["market", "Market & candidates"],
    ["score", "Scoreboard"],
    ["evolution", "Evolution"],
  ];
  return (
    <section className="panel" aria-label="Insights">
      <div className="tabs" role="tablist">
        {tabs.map(([id, label]) => (
          <button key={id} role="tab" className="tab" aria-selected={tab === id} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </div>
      <div className="panel-body" style={{ paddingTop: 14 }}>
        {tab === "report" && <Report text={snapshot.report} />}
        {tab === "market" && <Market snapshot={snapshot} />}
        {tab === "score" && <Score board={scoreboard} />}
        {tab === "evolution" && <Lineage evolution={evolution} />}
      </div>
    </section>
  );
}

function Report({ text }: { text: string | null }) {
  // The report is model output: render Markdown, then sanitise before it touches the DOM.
  const html = useMemo(() => (text ? DOMPurify.sanitize(marked.parse(text, { async: false })) : ""), [text]);
  if (!text) return <div className="secondary">No delivered report for this day.</div>;
  return <div className="report" dangerouslySetInnerHTML={{ __html: html }} />;
}

function Market({ snapshot }: { snapshot: Snapshot }) {
  const rows = Object.values(snapshot.market);
  const candidates = snapshot.candidates.candidates ?? [];
  const top = Math.max(1e-9, ...candidates.map((c) => c.score));
  const { universe_considered: considered, universe_returned: returned, universe_missing: missing } =
    snapshot.candidates;
  return (
    <div style={{ display: "grid", gap: 18 }}>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Ticker</th>
              <th>Class</th>
              <th className="r">Last</th>
              <th className="r">7d</th>
              <th className="r">30d</th>
              <th className="r">Vol 30d</th>
              <th className="r">From 52w high</th>
            </tr>
          </thead>
          <tbody className="num">
            {rows.map((m) => (
              <tr key={m.ticker}>
                <td>
                  <b>{m.ticker}</b>
                  {m.missing && <span className="muted"> · missing</span>}
                </td>
                <td className="secondary">{m.asset_class}</td>
                <td className="r">{m.last_price.toLocaleString()}</td>
                <td className="r">{pct(m.return_7d_pct)}</td>
                <td className="r">{pct(m.return_30d_pct)}</td>
                <td className="r">{(m.volatility_30d * 100).toFixed(1)}%</td>
                <td className="r">{pct(m.high_52w_distance_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <div className="panel-head" style={{ padding: "0 0 6px" }}>
          <h2>Screened candidates</h2>
          <span className="muted" style={{ fontSize: 12 }}>
            {returned ?? candidates.length} returned of {considered ?? "?"} considered
            {missing && Object.keys(missing).length > 0 && ` · ${Object.keys(missing).length} missing data`}
          </span>
        </div>
        <table className="data">
          <tbody className="num">
            {candidates.map((c) => (
              <tr key={c.ticker}>
                <td style={{ width: 80 }}>
                  <b>{c.ticker}</b>
                </td>
                <td className="secondary" style={{ width: 70 }}>
                  {c.asset_class}
                </td>
                <td>
                  <div className="scorebar" style={{ width: `${(c.score / top) * 100}%` }} />
                </td>
                <td className="r" style={{ width: 60 }}>
                  {c.score.toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Score({ board }: { board: Scoreboard | null }) {
  const [tip, setTip] = useState<{ call: ScoredCall; x: number; y: number } | null>(null);
  if (!board) {
    return (
      <div className="secondary">
        No scoreboard yet. Run <b>Score</b> from Mission control once some calls have matured (7 days).
      </div>
    );
  }
  const matured = board.calls.filter((c) => c.status === "matured" && c.action !== "hold" && c.excess_return_pct !== null);
  const rows = ["exit", "trim", "add", "new_idea"] as const;
  const extent = Math.max(1, ...matured.map((c) => Math.abs(c.excess_return_pct ?? 0)));
  const W = 640;
  const rowH = 34;
  const left = 74;
  const x = (v: number) => left + ((v + extent) / (2 * extent)) * (W - left - 16);
  return (
    <div>
      <div className="tiles">
        <Tile label="Hit rate (active calls)" value={board.hit_rate === null ? "—" : `${(board.hit_rate * 100).toFixed(0)}%`} hint="beat their benchmark" />
        <Tile
          label="Mean excess return"
          value={board.mean_excess_return_pct === null ? "—" : pct(board.mean_excess_return_pct, 2)}
          hint="vs benchmark, after costs"
        />
        <Tile label="Matured / pending" value={`${board.matured} / ${board.pending}`} hint={`${board.unscored} unscored`} />
        <Tile label="Active calls scored" value={String(board.active_calls)} hint={`as of ${board.generated_at}`} />
      </div>
      {matured.length > 0 && (
        <figure style={{ margin: 0 }}>
          <figcaption className="secondary" style={{ fontSize: 12, marginBottom: 4 }}>
            Excess return of each matured active call vs its benchmark (%). Right of zero = beat it.
          </figcaption>
          <svg viewBox={`0 0 ${W} ${rows.length * rowH + 28}`} width="100%" role="img" aria-label="Matured call outcomes">
            <line x1={x(0)} x2={x(0)} y1={4} y2={rows.length * rowH} stroke="var(--axis)" strokeWidth={1} />
            {rows.map((action, i) => (
              <g key={action}>
                <line x1={left} x2={W - 16} y1={i * rowH + rowH / 2} y2={i * rowH + rowH / 2} stroke="var(--grid)" />
                <text x={0} y={i * rowH + rowH / 2 + 4} fontSize={11} fill="var(--text-secondary)">
                  {ACTION_LABEL[action]}
                </text>
                {matured
                  .filter((c) => c.action === action)
                  .map((c, j) => (
                    <circle
                      key={`${c.ticker}-${c.task_date}-${j}`}
                      cx={x(c.excess_return_pct ?? 0)}
                      cy={i * rowH + rowH / 2}
                      r={5}
                      fill={(c.excess_return_pct ?? 0) >= 0 ? "var(--gain)" : "var(--loss)"}
                      stroke="var(--surface-1)"
                      strokeWidth={2}
                      onMouseMove={(e) => setTip({ call: c, x: e.clientX, y: e.clientY })}
                      onMouseLeave={() => setTip(null)}
                      style={{ cursor: "pointer" }}
                    />
                  ))}
              </g>
            ))}
            {[-extent, 0, extent].map((v) => (
              <text key={v} x={x(v)} y={rows.length * rowH + 18} fontSize={11} textAnchor="middle" fill="var(--text-muted)">
                {v === 0 ? "0" : pct(v, 1)}
              </text>
            ))}
          </svg>
        </figure>
      )}
      {tip && (
        <div className="tooltip" style={{ left: tip.x + 14, top: tip.y + 14 }}>
          <b>
            {tip.call.ticker} · {ACTION_LABEL[tip.call.action]}
          </b>
          <div className="row num">
            <span className="muted">Excess vs {tip.call.benchmark}</span>
            <span>{pct(tip.call.excess_return_pct ?? 0, 2)}</span>
          </div>
          <div className="row num">
            <span className="muted">Called → matured</span>
            <span>
              {tip.call.task_date} → {tip.call.maturity_date}
            </span>
          </div>
        </div>
      )}
      <details style={{ marginTop: 12 }}>
        <summary className="secondary" style={{ cursor: "pointer" }}>
          All scored calls ({board.calls.length})
        </summary>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Called</th>
                <th>Ticker</th>
                <th>Call</th>
                <th>Benchmark</th>
                <th className="r">Excess</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody className="num">
              {board.calls.map((c, i) => (
                <tr key={i}>
                  <td>{c.task_date}</td>
                  <td>
                    <b>{c.ticker}</b>
                  </td>
                  <td>{ACTION_LABEL[c.action]}</td>
                  <td>{c.benchmark}</td>
                  <td className="r">{c.excess_return_pct === null ? "—" : pct(c.excess_return_pct, 2)}</td>
                  <td className="secondary">{c.reason ? `${c.status} (${c.reason})` : c.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

function Tile({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      <div className="hint">{hint}</div>
    </div>
  );
}

function Lineage({ evolution }: { evolution: Evolution | null }) {
  if (!evolution || evolution.weeks.length === 0) {
    return (
      <div className="secondary">
        No weekly evolution runs yet. Run <b>Evolve</b> at the end of a week with daily snapshots.
      </div>
    );
  }
  const weeks = [...evolution.weeks].reverse();
  return (
    <div className="lineage">
      <p className="secondary" style={{ marginTop: 0 }}>
        Each week the agent proposes edits to its own harness, probes them on the week's snapshots, and adopts
        the composed bundle only if it delivers valid recommendations.
      </p>
      {weeks.map((w, i) => {
        const current = w.base === evolution.adopted_bundle;
        return (
          <div className="gen" key={w.week}>
            <div className="rail">
              <span className={`n${current ? " current" : ""}`} />
              {i < weeks.length - 1 && <span className="line" />}
            </div>
            <div className="card">
              <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                <b>{w.week}</b>
                {current && <span className="chip">✓ currently adopted base</span>}
              </div>
              <div className="secondary num" style={{ fontSize: 12 }}>
                {w.delivered}/{w.executions} executions delivered · started from{" "}
                <span className="mono">{w.base ? w.base.slice(0, 12) : "—"}</span>
              </div>
            </div>
          </div>
        );
      })}
      <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
        Adopted bundle: <span className="mono">{evolution.adopted_bundle?.slice(0, 16) ?? "none"}</span> ·{" "}
        {evolution.archived_bundles.length} archived
      </div>
    </div>
  );
}
