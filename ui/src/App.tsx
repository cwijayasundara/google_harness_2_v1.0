import { useCallback, useEffect, useState } from "react";
import { api, type Evolution, type Scoreboard, type Snapshot, type SnapshotSummary, type Status } from "./api";
import { CallCards } from "./components/CallCards";
import { Insights } from "./components/Insights";
import { MissionControl } from "./components/MissionControl";
import { PortfolioMap } from "./components/PortfolioMap";
import { Timeline } from "./components/Timeline";

type Theme = "system" | "light" | "dark";

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem("cockpit-theme") as Theme) || "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("cockpit-theme", theme);
    } catch {
      /* private mode: theme just isn't remembered */
    }
  }, [theme]);
  const next = () => setTheme((t) => (t === "system" ? "light" : t === "light" ? "dark" : "system"));
  return [theme, next];
}

export function App() {
  const [theme, cycleTheme] = useTheme();
  const [status, setStatus] = useState<Status | null>(null);
  const [snapshots, setSnapshots] = useState<SnapshotSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [scoreboard, setScoreboard] = useState<Scoreboard | null>(null);
  const [evolution, setEvolution] = useState<Evolution | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, list, board, evo] = await Promise.all([
        api.status(),
        api.snapshots(),
        api.scoreboard(),
        api.evolution(),
      ]);
      setStatus(s);
      setSnapshots(list);
      setScoreboard(board);
      setEvolution(evo);
      setSelected((current) =>
        current && list.some((d) => d.date === current) ? current : (list.at(-1)?.date ?? null),
      );
      setError(null);
    } catch (e) {
      setError(`Cannot reach the cockpit API (${(e as Error).message}). Is scripts/start-backend.sh running?`);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!selected) {
      setSnapshot(null);
      return;
    }
    setFocus(null);
    api.snapshot(selected).then(setSnapshot, (e) => setError((e as Error).message));
  }, [selected, snapshots]);

  const summary = snapshots.find((s) => s.date === selected);
  const portfolio = snapshot?.portfolio;
  const ageHours = portfolio ? (Date.now() - Date.parse(portfolio.fetched_at)) / 36e5 : null;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden />
          Portfolio Cockpit
        </div>
        <span className="chip notice" title="This tool never places, modifies, or cancels orders.">
          ⚠ Research only — no orders are placed
        </span>
        {status?.demo && <span className="chip">◆ Synthetic demo data</span>}
        <span className="spacer" />
        {status && (
          <>
            <StatusChip ok={status.docker} label="Docker" />
            <StatusChip ok={status.keys.fireworks} label="Fireworks key" />
            <StatusChip ok={status.keys.etoro} label="eToro key" />
            <span className="chip mono" title={status.adopted_bundle ?? "No adopted bundle yet"}>
              bundle {status.adopted_bundle ? status.adopted_bundle.slice(0, 8) : "—"}
            </span>
          </>
        )}
        <button className="icon-btn" onClick={cycleTheme} title="Theme: system → light → dark">
          {theme === "system" ? "◐ Auto" : theme === "light" ? "☀ Light" : "☾ Dark"}
        </button>
      </header>

      {error && (
        <div className="error" style={{ padding: "10px 20px" }}>
          {error}
        </div>
      )}

      {status && snapshots.length === 0 ? (
        <Empty baseDir={status.base_dir} />
      ) : (
        <main className="layout">
          <Timeline snapshots={snapshots} selected={selected} evolution={evolution} onSelect={setSelected} />
          <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
            {snapshot && portfolio && (
              <section className="panel" aria-label="Briefing">
                <div className="panel-body" style={{ paddingTop: 14 }}>
                  <div className="tiles" style={{ marginBottom: 0 }}>
                    <Brief
                      label="Snapshot"
                      value={new Date(snapshot.date + "T00:00:00").toLocaleDateString(undefined, {
                        weekday: "long",
                        day: "numeric",
                        month: "short",
                      })}
                      hint={`fetched ${portfolio.fetched_at.replace("T", " ").replace("Z", " UTC")}${portfolio.proxy ? " · proxy holdings" : ""}`}
                    />
                    <Brief label="Holdings" value={String(portfolio.positions.length)} hint={`${(portfolio.cash_weight * 100).toFixed(1)}% cash`} />
                    <Brief
                      label="Active calls"
                      value={summary?.has_recommendations ? String(summary.non_hold_calls) : "—"}
                      hint={summary?.has_recommendations ? "max 5 per day" : "not analysed yet"}
                    />
                    <Brief
                      label="Snapshot age"
                      value={ageHours === null ? "—" : ageHours < 48 ? `${ageHours.toFixed(0)}h` : `${(ageHours / 24).toFixed(0)}d`}
                      hint="evidence is frozen at fetch time"
                    />
                  </div>
                </div>
              </section>
            )}
            {snapshot && <PortfolioMap snapshot={snapshot} focus={focus} onFocus={setFocus} />}
            {snapshot && <Insights snapshot={snapshot} scoreboard={scoreboard} evolution={evolution} />}
          </div>
          {snapshot && <CallCards snapshot={snapshot} focus={focus} onFocus={setFocus} />}
        </main>
      )}

      <MissionControl status={status} selectedDate={selected} onJobFinished={refresh} />
    </div>
  );
}

function StatusChip({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="chip" title={ok ? `${label}: available` : `${label}: not configured`}>
      <span className="dot" style={{ background: ok ? "var(--good)" : "var(--critical)" }} />
      {label} {ok ? "✓" : "✕"}
    </span>
  );
}

function Brief({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value" style={{ fontSize: 20 }}>
        {value}
      </div>
      <div className="hint">{hint}</div>
    </div>
  );
}

function Empty({ baseDir }: { baseDir: string }) {
  return (
    <div className="empty">
      <h2>No snapshots in {baseDir} yet</h2>
      <p>
        Open <b>Mission control</b> (bottom right) and fetch your first snapshot, or explore with synthetic data:
      </p>
      <p>
        <code>scripts/setup.sh --demo</code> then <code>scripts/start-backend.sh --demo</code>
      </p>
    </div>
  );
}
