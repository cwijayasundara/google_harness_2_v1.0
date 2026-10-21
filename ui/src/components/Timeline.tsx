import { useEffect } from "react";
import type { Evolution, SnapshotSummary } from "../api";

interface Props {
  snapshots: SnapshotSummary[];
  selected: string | null;
  evolution: Evolution | null;
  onSelect: (day: string) => void;
}

/** The "time machine": every frozen snapshot, grouped by ISO week. ←/→ step through days. */
export function Timeline({ snapshots, selected, evolution, onSelect }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      const i = snapshots.findIndex((s) => s.date === selected);
      const next = snapshots[i + (e.key === "ArrowRight" ? 1 : -1)];
      if (next) onSelect(next.date);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [snapshots, selected, onSelect]);

  const weeks = new Map<string, SnapshotSummary[]>();
  for (const s of [...snapshots].reverse()) weeks.set(s.week, [...(weeks.get(s.week) ?? []), s]);
  const evolved = new Map(evolution?.weeks.map((w) => [w.week, w]) ?? []);

  return (
    <nav className="panel timeline" aria-label="Snapshot timeline">
      <div className="panel-head">
        <h2>Time machine</h2>
      </div>
      <div className="panel-body" style={{ paddingTop: 0 }}>
        {snapshots.length === 0 && <div className="muted">No snapshots yet.</div>}
        {[...weeks.entries()].map(([week, days]) => {
          const run = evolved.get(week);
          return (
            <div className="week" key={week}>
              <div className="week-label">
                <span>{week}</span>
                {run && (
                  <span className="adopt-flag" title={`${run.delivered}/${run.executions} executions delivered`}>
                    ⟳ evolved
                  </span>
                )}
              </div>
              {days.map((d) => (
                <button
                  key={d.date}
                  className={`day${d.date === selected ? " active" : ""}${d.has_recommendations ? " has-recs" : ""}`}
                  onClick={() => onSelect(d.date)}
                  aria-current={d.date === selected ? "date" : undefined}
                  title={d.has_recommendations ? `${d.non_hold_calls} active calls` : "Snapshot only"}
                >
                  <span className="node" />
                  <span className="num">
                    {new Date(d.date + "T00:00:00").toLocaleDateString(undefined, {
                      weekday: "short",
                      day: "numeric",
                      month: "short",
                    })}
                  </span>
                  <span className="calls" aria-label={`${d.non_hold_calls} active calls`}>
                    {Array.from({ length: Math.min(d.non_hold_calls, 5) }, (_, i) => (
                      <i key={i} />
                    ))}
                  </span>
                </button>
              ))}
            </div>
          );
        })}
        {snapshots.length > 1 && <div className="muted" style={{ fontSize: 11 }}>Tip: ← / → to scrub</div>}
      </div>
    </nav>
  );
}
