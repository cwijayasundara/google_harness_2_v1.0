import { resolveEvidence, type Recommendation, type Snapshot } from "../api";
import { ACTION_LABEL } from "./PortfolioMap";

const ORDER = { exit: 0, trim: 1, add: 2, new_idea: 3, hold: 4 } as const;

interface Props {
  snapshot: Snapshot;
  focus: string | null;
  onFocus: (ticker: string | null) => void;
}

export function CallCards({ snapshot, focus, onFocus }: Props) {
  const recs = snapshot.recommendations;
  if (!recs) {
    return (
      <section className="panel calls-col" aria-label="Calls">
        <div className="panel-head">
          <h2>Agent calls</h2>
        </div>
        <div className="panel-body secondary">
          No recommendations for this snapshot yet. Run <b>Daily</b> from Mission control to have the
          adopted bundle analyse it.
        </div>
      </section>
    );
  }
  const active = recs.filter((r) => r.action !== "hold").sort((a, b) => ORDER[a.action] - ORDER[b.action]);
  const holds = recs.filter((r) => r.action === "hold");
  const weights = new Map(snapshot.portfolio?.positions.map((p) => [p.ticker, p.portfolio_weight]) ?? []);

  return (
    <div className="calls-col" aria-label="Calls">
      <div className="panel-head" style={{ padding: "0 2px" }}>
        <h2>Agent calls</h2>
        <span className="muted" style={{ fontSize: 12 }}>
          {active.length} active · {holds.length} hold · 7-day horizon
        </span>
      </div>
      {active.map((rec) => (
        <Card
          key={rec.ticker + rec.action}
          rec={rec}
          snapshot={snapshot}
          current={weights.get(rec.ticker) ?? 0}
          focused={focus === rec.ticker}
          onFocus={onFocus}
        />
      ))}
      {holds.length > 0 && (
        <div className="holds">
          Holding: {holds.map((h) => h.ticker).join(", ")}
        </div>
      )}
    </div>
  );
}

function Card({
  rec,
  snapshot,
  current,
  focused,
  onFocus,
}: {
  rec: Recommendation;
  snapshot: Snapshot;
  current: number;
  focused: boolean;
  onFocus: (ticker: string | null) => void;
}) {
  return (
    <article
      className={`call${focused ? " focused" : ""}`}
      onClick={() => onFocus(focused ? null : rec.ticker)}
      onMouseEnter={() => onFocus(rec.ticker)}
    >
      <div className="call-head">
        <span className="tk">{rec.ticker}</span>
        <span className="badge">{ACTION_LABEL[rec.action]}</span>
        <span className="weight-shift num" title="Current → target weight">
          {(current * 100).toFixed(1)}% → <b>{rec.target_weight_pct.toFixed(1)}%</b>
        </span>
      </div>
      <p>{rec.thesis}</p>
      {rec.invalidation && <p className="inval">Invalidated if: {rec.invalidation}</p>}
      <div className="refs">
        <span className="ref" title="Benchmark">
          vs {rec.benchmark}
        </span>
        {rec.evidence_refs.map((ref) => {
          const resolved = resolveEvidence(ref, snapshot);
          return (
            <span key={ref} className="ref" tabIndex={0}>
              {ref.replace(/\.json:/, ":")}
              <span className="lens" role="tooltip">
                {resolved ? (
                  <>
                    <span className="muted" style={{ color: "inherit", opacity: 0.7 }}>
                      {resolved.label}
                    </span>
                    <br />
                    <b>{resolved.value}</b>
                  </>
                ) : (
                  "Not found in this snapshot"
                )}
              </span>
            </span>
          );
        })}
      </div>
    </article>
  );
}
