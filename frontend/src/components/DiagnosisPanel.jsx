import { IconDiagnose } from "./Icons";
import { inrShort } from "../lib/format";

function Meter({ label, pct, color }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div className="cmp-top">
        <span className="k">{label}</span>
        <b style={{ fontVariantNumeric: "tabular-nums" }}>{pct}%</b>
      </div>
      <div className="track">
        <div className="fill" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}

export default function DiagnosisPanel({ diagnosis, llm, promises }) {
  if (!diagnosis) return null;

  return (
    <div className="panel">
      <h2><IconDiagnose size={15} />How failures were diagnosed</h2>
      <Meter label="Rule engine — free, deterministic"
             pct={diagnosis.rule_engine_pct} color="var(--brand)" />
      <Meter label="LLM — the ambiguous free-text tail"
             pct={diagnosis.llm_pct} color="var(--good)" />
      <Meter label="Unclassified → human review"
             pct={diagnosis.unclassified_pct} color="var(--warning)" />

      <div className="chart-note">
        {llm?.enabled ? (
          <>The LLM ran {llm.calls} times for ${llm.cost_usd}. It is asked only when
            the rule engine abstains.</>
        ) : (
          <>No <code>ANTHROPIC_API_KEY</code> is set, so the LLM tier is off and its{" "}
            {diagnosis.unclassified_count} ambiguous cases go to humans instead of
            being guessed. Setting a key recovers them.</>
        )}
      </div>

      <h2 style={{ marginTop: 20 }}>Promise-to-pay tracker</h2>
      {promises?.kept_rate_pct != null ? (
        <div className="controls">
          <span className="badge b-good">{promises.kept_rate_pct}% kept</span>
          <span className="badge b-muted">
            {inrShort(promises.total_promised_amount)} committed
          </span>
        </div>
      ) : (
        <div style={{ color: "var(--muted)", fontSize: 12.5 }}>
          No promises negotiated in this batch.
        </div>
      )}
    </div>
  );
}
