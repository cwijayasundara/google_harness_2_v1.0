import { hierarchy, treemap, treemapSquarify } from "d3-hierarchy";
import { useEffect, useMemo, useRef, useState } from "react";
import { pct, type Action, type Recommendation, type Snapshot } from "../api";

export const ACTION_LABEL: Record<Action, string> = {
  hold: "• HOLD",
  trim: "▼ TRIM",
  add: "▲ ADD",
  new_idea: "✦ NEW",
  exit: "✕ EXIT",
};

interface Item {
  key: string;
  name: string;
  kind: "position" | "cash" | "idea";
  now: number;
  proposed: number;
  pnl: number | null;
  price: number | null;
  units: number | null;
  rec: Recommendation | null;
}

/** Holdings, cash, and new ideas with their current and proposed weights (fractions). */
export function buildItems(snapshot: Snapshot): Item[] {
  const portfolio = snapshot.portfolio;
  if (!portfolio) return [];
  const recs = new Map((snapshot.recommendations ?? []).map((r) => [r.ticker, r]));
  const items: Item[] = portfolio.positions.map((p) => {
    const rec = recs.get(p.ticker) ?? null;
    const proposed = !rec ? p.portfolio_weight : rec.action === "exit" ? 0 : rec.target_weight_pct / 100;
    return {
      key: p.ticker,
      name: p.instrument,
      kind: "position",
      now: p.portfolio_weight,
      proposed,
      pnl: p.unrealized_pnl_pct,
      price: p.current_price,
      units: p.units,
      rec,
    };
  });
  const held = new Set(items.map((i) => i.key));
  for (const rec of recs.values()) {
    if (held.has(rec.ticker) || rec.action === "exit" || rec.target_weight_pct <= 0) continue;
    items.push({
      key: rec.ticker,
      name: rec.ticker,
      kind: "idea",
      now: 0,
      proposed: rec.target_weight_pct / 100,
      pnl: null,
      price: snapshot.market[rec.ticker]?.last_price ?? null,
      units: null,
      rec,
    });
  }
  const invested = items.reduce((s, i) => s + i.proposed, 0);
  if (invested > 1) items.forEach((i) => (i.proposed /= invested));
  items.push({
    key: "CASH",
    name: `Cash (${portfolio.currency})`,
    kind: "cash",
    now: portfolio.cash_weight,
    proposed: Math.max(0, 1 - Math.min(1, invested)),
    pnl: null,
    price: null,
    units: null,
    rec: null,
  });
  return items;
}

function fill(item: Item): string {
  if (item.kind !== "position" || item.pnl === null) return "var(--surface-2)";
  // Diverging blue (gain) / red (loss) through a neutral gray; saturates at ±50%.
  const strength = Math.min(100, (Math.abs(item.pnl) / 0.5) * 100);
  const pole = item.pnl >= 0 ? "var(--gain)" : "var(--loss)";
  return `color-mix(in oklab, ${pole} ${strength.toFixed(0)}%, var(--neutral))`;
}

interface Props {
  snapshot: Snapshot;
  focus: string | null;
  onFocus: (ticker: string | null) => void;
}

export function PortfolioMap({ snapshot, focus, onFocus }: Props) {
  const [mode, setMode] = useState<"now" | "proposed">("now");
  const [view, setView] = useState<"map" | "table">("map");
  const [size, setSize] = useState({ w: 800, h: 440 });
  const [tip, setTip] = useState<{ item: Item; x: number; y: number } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const items = useMemo(() => buildItems(snapshot), [snapshot]);
  const hasRecs = Boolean(snapshot.recommendations?.length);

  useEffect(() => {
    if (!hasRecs) setMode("now");
  }, [hasRecs]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) =>
      setSize({ w: entry.contentRect.width, h: entry.contentRect.height }),
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [view]);

  const layout = useMemo(() => {
    const root = hierarchy<{ children?: Item[] } | Item>({ children: items }, (d) =>
      "children" in d ? d.children : undefined,
    ).sum((d) => ("key" in d ? Math.max(0, mode === "now" ? d.now : d.proposed) : 0));
    // Stable order across modes keeps each cell's neighbourhood readable while it morphs.
    root.sort((a, b) => ((b.data as Item).now ?? 0) - ((a.data as Item).now ?? 0));
    treemap<{ children?: Item[] } | Item>()
      .tile(treemapSquarify.ratio(1.2))
      .size([size.w, size.h])
      .paddingInner(2)
      .round(true)(root);
    const placed = new Map<string, { x: number; y: number; w: number; h: number }>();
    for (const leaf of root.leaves()) {
      const n = leaf as unknown as { x0: number; y0: number; x1: number; y1: number; data: Item };
      placed.set(n.data.key, { x: n.x0, y: n.y0, w: n.x1 - n.x0, h: n.y1 - n.y0 });
    }
    return placed;
  }, [items, mode, size]);

  return (
    <section className="panel" aria-label="Portfolio map">
      <div className="panel-head">
        <h2>Portfolio map</h2>
        <span className="muted" style={{ fontSize: 12 }}>
          size = weight · colour = unrealised P&amp;L
        </span>
        <div className="map-toolbar">
          <div className="segmented" role="group" aria-label="Weights">
            <button aria-pressed={mode === "now"} onClick={() => setMode("now")}>
              Now
            </button>
            <button
              aria-pressed={mode === "proposed"}
              onClick={() => setMode("proposed")}
              disabled={!hasRecs}
              title={hasRecs ? "Morph into the agent's target weights" : "No recommendations for this day"}
            >
              Proposed
            </button>
          </div>
          <div className="segmented" role="group" aria-label="View">
            <button aria-pressed={view === "map"} onClick={() => setView("map")}>
              Map
            </button>
            <button aria-pressed={view === "table"} onClick={() => setView("table")}>
              Table
            </button>
          </div>
        </div>
      </div>
      <div className="panel-body">
        {view === "map" ? (
          <div className="treemap" ref={box} onMouseLeave={() => setTip(null)}>
            {items.map((item) => {
              const r = layout.get(item.key) ?? { x: 0, y: 0, w: 0, h: 0 };
              const weight = mode === "now" ? item.now : item.proposed;
              const cls = [
                "cell",
                item.kind === "cash" && "cash",
                item.kind === "idea" && "idea",
                (r.w < 96 || r.h < 64) && "tiny",
                (r.w < 40 || r.h < 28) && "micro",
                focus === item.key && "focused",
                focus && focus !== item.key && "dimmed",
              ]
                .filter(Boolean)
                .join(" ");
              return (
                <div
                  key={item.key}
                  className={cls}
                  role="button"
                  tabIndex={0}
                  aria-label={`${item.key} ${(weight * 100).toFixed(1)} percent`}
                  style={{
                    left: r.x,
                    top: r.y,
                    width: r.w,
                    height: r.h,
                    opacity: r.w * r.h > 0 ? undefined : 0,
                    background: fill(item),
                  }}
                  onClick={() => onFocus(focus === item.key ? null : item.key)}
                  onKeyDown={(e) => e.key === "Enter" && onFocus(focus === item.key ? null : item.key)}
                  onMouseMove={(e) => setTip({ item, x: e.clientX, y: e.clientY })}
                >
                  <div>
                    <div className="tk">{item.key}</div>
                    <div className="sub num">
                      {(weight * 100).toFixed(1)}%
                      {item.pnl !== null && <> · {pct(item.pnl * 100)}</>}
                    </div>
                  </div>
                  {item.rec && item.rec.action !== "hold" && (
                    <span className="act">{ACTION_LABEL[item.rec.action]}</span>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <WeightsTable items={items} />
        )}
        <div className="legend">
          <span>Loss −50%</span>
          <span className="ramp" aria-hidden />
          <span>Gain +50%</span>
          <span className="spacer" />
          <span>✦ dashed = new idea · hatched = cash</span>
        </div>
      </div>
      {tip && view === "map" && <Tooltip {...tip} />}
    </section>
  );
}

function Tooltip({ item, x, y }: { item: Item; x: number; y: number }) {
  const left = Math.min(x + 14, window.innerWidth - 220);
  return (
    <div className="tooltip" style={{ left, top: y + 14 }}>
      <div style={{ fontWeight: 700 }}>{item.key}</div>
      <div className="muted" style={{ marginBottom: 6 }}>
        {item.name}
      </div>
      <div className="row num">
        <span className="muted">Weight now</span>
        <span>{(item.now * 100).toFixed(1)}%</span>
      </div>
      <div className="row num">
        <span className="muted">Proposed</span>
        <span>{(item.proposed * 100).toFixed(1)}%</span>
      </div>
      {item.pnl !== null && (
        <div className="row num">
          <span className="muted">Unrealised P&amp;L</span>
          <span>{pct(item.pnl * 100)}</span>
        </div>
      )}
      {item.price !== null && (
        <div className="row num">
          <span className="muted">Price</span>
          <span>{item.price.toLocaleString()}</span>
        </div>
      )}
      {item.rec && (
        <div className="row">
          <span className="muted">Call</span>
          <span>{ACTION_LABEL[item.rec.action]}</span>
        </div>
      )}
    </div>
  );
}

function WeightsTable({ items }: { items: Item[] }) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Name</th>
            <th className="r">Now</th>
            <th className="r">Proposed</th>
            <th className="r">P&amp;L</th>
            <th>Call</th>
          </tr>
        </thead>
        <tbody className="num">
          {items.map((i) => (
            <tr key={i.key}>
              <td>
                <b>{i.key}</b>
              </td>
              <td className="secondary">{i.name}</td>
              <td className="r">{(i.now * 100).toFixed(1)}%</td>
              <td className="r">{(i.proposed * 100).toFixed(1)}%</td>
              <td className="r">{i.pnl === null ? "—" : pct(i.pnl * 100)}</td>
              <td>{i.rec ? ACTION_LABEL[i.rec.action] : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
