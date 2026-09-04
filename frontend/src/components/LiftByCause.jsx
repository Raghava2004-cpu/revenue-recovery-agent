import { IconChart } from "./Icons";
import { inrShort, titleise } from "../lib/format";

/**
 * Diverging bars: rupees the agent recovered that the baseline did not.
 *
 * Deliberately shows the negative and zero rows too. The causes where the agent
 * adds nothing — transient gateway timeouts a dumb retry already handles, and
 * risk-blocked cases where the compliant recovery rate is zero — are the rows
 * that make the positive ones believable.
 */
export default function LiftByCause({ causes }) {
  const rows = (causes ?? []).filter((c) => c.count > 0);
  if (!rows.length) {
    return (
      <div className="panel">
        <h2>Where the lift comes from</h2>
        <div className="empty">No data.</div>
      </div>
    );
  }

  const max = Math.max(...rows.map((c) => Math.abs(c.incremental_amount)), 1);

  return (
    <div className="panel">
      <h2><IconChart size={15} />Where the lift comes from — incremental ₹ by root cause</h2>
      {rows.map((c) => {
        const v = c.incremental_amount;
        const w = `${(Math.abs(v) / max) * 50}%`;
        return (
          <div
            key={c.root_cause}
            className="dv"
            title={`${c.label} — agent ${c.agent_rate_pct}% vs baseline ${c.baseline_rate_pct}% over ${c.count} cases`}
          >
            <span className="name">{titleise(c.root_cause)}</span>
            <span className="dv-track">
              <span className="zero" />
              <span
                className="bar"
                style={
                  v >= 0
                    ? { left: "50%", width: w, background: "var(--agent)" }
                    : { right: "50%", width: w, background: "var(--danger)" }
                }
              />
            </span>
            <span
              className="amt"
              style={{
                color: v > 0 ? "var(--good)" : v < 0 ? "var(--danger)" : undefined,
              }}
            >
              {v === 0 ? "—" : inrShort(v)}
            </span>
          </div>
        );
      })}
      <div className="chart-note">
        Rupees the agent recovered that the baseline did not, on the same customers.
        Zero means both policies did equally well — the agent adds nothing there,
        and says so.
      </div>
    </div>
  );
}
