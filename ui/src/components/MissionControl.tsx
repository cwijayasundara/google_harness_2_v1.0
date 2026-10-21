import { useEffect, useState } from "react";
import { api, type Command, type Job, type JobRequest, type Status } from "../api";

interface Props {
  status: Status | null;
  selectedDate: string | null;
  onJobFinished: () => void;
}

/** Launches the existing CLI commands. Paid commands need a typed cap and an explicit tick. */
export function MissionControl({ status, selectedDate, onJobFinished }: Props) {
  const [open, setOpen] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState<"etoro" | "csv">("csv");
  const [csv, setCsv] = useState("");
  const [cap, setCap] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [useDate, setUseDate] = useState(false);

  useEffect(() => {
    if (!job || job.status !== "running") return;
    const timer = setInterval(async () => {
      const next = await api.job(job.id);
      setJob(next);
      if (next.status !== "running") onJobFinished();
    }, 1000);
    return () => clearInterval(timer);
  }, [job, onJobFinished]);

  // Demo data is read-only: the backend refuses jobs there, so the UI says so up front.
  const running = job?.status === "running" || Boolean(status?.job_running) || Boolean(status?.demo);
  const maxUsd = Number(status?.max_ui_usd ?? 25);
  const capValue = Number(cap);
  const capOk = cap !== "" && capValue > 0 && capValue <= maxUsd;
  const paidReady = capOk && confirmed && Boolean(status?.keys.fireworks) && Boolean(status?.docker);

  async function launch(command: Command) {
    setError(null);
    const body: JobRequest = { command };
    if (useDate && selectedDate) body.date = selectedDate;
    if ((command === "fetch" || command === "daily") && source === "csv" && csv.trim()) body.csv = csv;
    if (command === "daily" || command === "evolve") {
      body.max_usd = cap;
      body.confirm_paid = confirmed;
    }
    try {
      setJob(await api.startJob(body));
      setConfirmed(false);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="dock">
      {open && (
        <div className="panel drawer" role="dialog" aria-label="Mission control">
          <div className="panel-head">
            <h2>Mission control</h2>
            <span className="spacer" />
            <button className="icon-btn" onClick={() => setOpen(false)} aria-label="Close">
              ✕
            </button>
          </div>
          <div className="panel-body secondary" style={{ fontSize: 12, paddingTop: 0 }}>
            Runs the <span className="mono">harness-clone portfolio</span> commands on this machine.
            Recommendations only — nothing here places orders.
          </div>

          {status?.demo && (
            <div className="mc-section secondary" style={{ fontSize: 12 }}>
              ◆ You are viewing synthetic demo data, which is read-only. Start the backend without{" "}
              <span className="mono">--demo</span> to run commands against your own portfolio.
            </div>
          )}

          <div className="mc-section">
            <label className="field" style={{ marginTop: 0 }}>
              <input type="checkbox" checked={useDate} onChange={(e) => setUseDate(e.target.checked)} />
              <span>
                Act on the selected day ({selectedDate ?? "none"}) instead of today
              </span>
            </label>
          </div>

          <div className="mc-section">
            <h3>
              1 · Fetch snapshot <span className="cost free">free</span>
            </h3>
            <div className="field">
              <div className="segmented" role="group" aria-label="Portfolio source">
                <button aria-pressed={source === "csv"} onClick={() => setSource("csv")}>
                  CSV
                </button>
                <button
                  aria-pressed={source === "etoro"}
                  onClick={() => setSource("etoro")}
                  title={status?.keys.etoro ? "" : "ETORO_API_KEY is not set in the backend's environment"}
                >
                  eToro API {status?.keys.etoro ? "" : "(no key)"}
                </button>
              </div>
            </div>
            {source === "csv" && (
              <div className="field">
                <textarea
                  value={csv}
                  onChange={(e) => setCsv(e.target.value)}
                  placeholder={"instrument,ticker,asset_class,units,open_rate,current_price\nApple Inc,AAPL,equity,10,150,190\nCash,CASH,cash,500,1,1"}
                />
                <input
                  type="file"
                  accept=".csv,text/csv"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (file) setCsv(await file.text());
                  }}
                />
              </div>
            )}
            <div className="field">
              <button
                className="primary-btn"
                disabled={running || (source === "csv" && !csv.trim()) || (source === "etoro" && !status?.keys.etoro)}
                onClick={() => launch("fetch")}
              >
                Fetch
              </button>
            </div>
          </div>

          <div className="mc-section">
            <h3>
              2 · Paid runs <span className="cost paid">uses Fireworks credit</span>
            </h3>
            <div className="field">
              <span>Spending cap $</span>
              <input
                type="number"
                min="0.01"
                max={maxUsd}
                step="0.5"
                value={cap}
                onChange={(e) => setCap(e.target.value)}
                style={{ width: 90 }}
                aria-label="Spending cap in US dollars"
              />
              <span className="muted">max ${maxUsd} from the UI</span>
            </div>
            <label className="field">
              <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
              <span>I understand this makes paid model calls up to this cap.</span>
            </label>
            {!status?.keys.fireworks && <div className="error">FIREWORKS_API_KEY is not set for the backend.</div>}
            {!status?.docker && <div className="error">Docker was not found on the backend host.</div>}
            <div className="field">
              <button className="primary-btn" disabled={running || !paidReady} onClick={() => launch("daily")}>
                Run daily
              </button>
              <button className="primary-btn" disabled={running || !paidReady} onClick={() => launch("evolve")}>
                Evolve week
              </button>
            </div>
          </div>

          <div className="mc-section">
            <h3>
              3 · Score matured calls <span className="cost free">free</span>
            </h3>
            <div className="field">
              <button className="primary-btn" disabled={running} onClick={() => launch("score")}>
                Score
              </button>
            </div>
          </div>

          {(job || error) && (
            <div className="mc-section">
              {error && <div className="error">{error}</div>}
              {job && (
                <>
                  <div className="status-line">
                    <span
                      className="dot"
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: "50%",
                        background:
                          job.status === "running" ? "var(--warning)" : job.status === "succeeded" ? "var(--good)" : "var(--critical)",
                      }}
                    />
                    <b>{job.command}</b> {job.status === "running" ? "running…" : job.status}
                    {job.returncode !== null && <span className="muted"> (exit {job.returncode})</span>}
                  </div>
                  <div className="console">{job.log_tail || "…"}</div>
                </>
              )}
            </div>
          )}
        </div>
      )}
      <button className="primary-btn dock-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {job?.status === "running" || status?.job_running ? "● Job running" : "⌘ Mission control"}
      </button>
    </div>
  );
}
