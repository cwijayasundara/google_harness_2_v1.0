import { useEffect, useState } from "react";
import { api, type DreamReport, type MemoryWiki } from "../api";

/** The offline dream: shows the memory wiki agents read at startup, and runs
 * the deterministic consolidation (audit + restatements + wiki) on demand.
 * The dream makes no model calls and spends nothing. */
export function MemoryPanel({ onChanged }: { onChanged: () => void }) {
  const [wiki, setWiki] = useState<MemoryWiki | null>(null);
  const [report, setReport] = useState<DreamReport | null>(null);
  const [running, setRunning] = useState(false);
  const [adopt, setAdopt] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<string | null>(null);

  useEffect(() => {
    api.memory().then(setWiki, (e) => setError((e as Error).message));
  }, []);

  const files = Object.keys(wiki?.files ?? {});
  const active = tab && files.includes(tab) ? tab : files[0] ?? null;

  async function runDream() {
    setRunning(true);
    setError(null);
    try {
      const next = await api.dream({ adopt });
      setReport(next);
      setWiki(await api.memory());
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="panel" aria-label="Memory and dreaming">
      <div className="panel-head">
        <h2>Memory &amp; dreaming</h2>
        <span className="spacer" />
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
          <input type="checkbox" checked={adopt} onChange={(e) => setAdopt(e.target.checked)} />
          adopt wiki into next runs
        </label>
        <button className="btn" onClick={runDream} disabled={running}>
          {running ? "Dreaming…" : "Run dream"}
        </button>
      </div>
      <div className="panel-body" style={{ display: "grid", gap: 12 }}>
        {wiki && (
          <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
            {wiki.adopted_bundle
              ? `Wiki served by adopted bundle ${wiki.adopted_bundle.slice(0, 8)}`
              : "No adopted bundle yet — run a daily task first"}
          </div>
        )}
        {files.length > 0 && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {files.map((name) => (
              <button
                key={name}
                className={`chip${active === name ? " notice" : ""}`}
                onClick={() => setTab(name)}
                style={{ cursor: "pointer" }}
              >
                {name.replace("memory/", "")}
              </button>
            ))}
          </div>
        )}
        {active && (
          <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 12, lineHeight: 1.5 }}>
            {wiki?.files[active]}
          </pre>
        )}
        {report && (
          <div style={{ display: "grid", gap: 8 }}>
            <div className="tiles" style={{ marginBottom: 0 }}>
              <Brief label="Entries" value={String(report.audit.entries)} hint="experience entries audited" />
              <Brief label="Restated" value={String(report.restated)} hint="corrections + verified" />
              <Brief label="Merged" value={String(report.merged)} hint="duplicates collapsed" />
              <Brief label="Conflicts" value={String(report.conflicts_marked)} hint="marked, never guessed" />
            </div>
            {report.patch_digest ? (
              <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                Wiki published as bundle <span className="mono">{report.patch_digest.slice(0, 8)}</span>
                {report.adopted ? " and adopted" : " (not adopted)"}. Run again: restated 0 means the dream converged.
              </div>
            ) : (
              <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                Nothing to consolidate — no restatements, no new bundle.
              </div>
            )}
            {report.audit.findings.length > 0 && (
              <details>
                <summary style={{ cursor: "pointer", fontSize: 12 }}>
                  {report.audit.findings.length} audit findings
                </summary>
                <ul style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: 12 }}>
                  {report.audit.findings.map((f, i) => (
                    <li key={i}>
                      <span className="chip mono">{f.status}</span> {f.detail || "no findings"}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
        {error && <div className="error" style={{ padding: 8 }}>{error}</div>}
      </div>
    </section>
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
