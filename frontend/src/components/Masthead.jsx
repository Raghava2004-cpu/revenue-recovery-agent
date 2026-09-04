import { plural } from "../lib/format";

function Badge({ tone, children }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export default function Masthead({ metrics, audit, busy, size, onSize, onRun, onRefresh, onReset }) {
  const dx = metrics?.diagnosis;
  const llm = metrics?.llm;

  return (
    <header className="masthead">
      <div>
        <div className="mark">
          <span className="glyph" aria-hidden="true">₹</span>
          <h1>AI Revenue Recovery Agent</h1>
        </div>
        <div className="tagline">
          Detect → Diagnose → Decide → Act &nbsp;·&nbsp; every case run under two policies
          on the same customers &nbsp;·&nbsp; Razorpay Buildathon, Track 03
        </div>
        <div className="controls" style={{ marginTop: 10 }}>
          <Badge tone="b-muted">policy {metrics?.policy_version ?? "—"}</Badge>

          {audit && (
            <Badge tone={audit.valid ? "b-good" : "b-danger"}>
              <span className="dot" />
              {audit.valid
                ? `audit chain verified · ${audit.entries.toLocaleString()} entries`
                : `audit chain BROKEN at #${audit.broken_at_seq}`}
            </Badge>
          )}

          {dx && (
            <Badge tone={llm?.enabled ? "b-brand" : "b-warning"}>
              {llm?.enabled
                ? `diagnosis: ${dx.rule_engine_pct}% rules · ${dx.llm_pct}% LLM · $${llm.cost_usd}`
                : `diagnosis: ${dx.rule_engine_pct}% rules · LLM off, ${dx.unclassified_pct}% to humans`}
            </Badge>
          )}
        </div>
      </div>

      <div className="controls">
        <select
          value={size}
          onChange={(e) => onSize(Number(e.target.value))}
          aria-label="Batch size"
          title="Batch size"
        >
          <option value={60}>60 cases (fast)</option>
          <option value={250}>250 cases (significant)</option>
          <option value={500}>500 cases</option>
        </select>
        <button type="button" className="primary" onClick={onRun} disabled={busy}>
          {busy ? <><span className="spinner" />Running {plural(size, "case")}…</> : "Run batch"}
        </button>
        <button type="button" onClick={onRefresh} disabled={busy}>Refresh</button>
        <button type="button" onClick={onReset} disabled={busy}>Reset</button>
      </div>
    </header>
  );
}
