import { IconShield } from "./Icons";
import { titleise } from "../lib/format";

function RuleList({ rules }) {
  const entries = Object.entries(rules ?? {}).sort((a, b) => b[1] - a[1]);
  if (!entries.length) {
    return <div style={{ color: "var(--muted)", fontSize: 12.5 }}>none</div>;
  }
  return entries.map(([rule, n]) => (
    <div className="rule-row" key={rule}>
      <span>{titleise(rule)}</span>
      <b>{n}</b>
    </div>
  ));
}

export default function CompliancePanel({ compliance }) {
  if (!compliance) return null;

  return (
    <div className="panel">
      <h2><IconShield size={15} />Compliance guardrails</h2>
      <div className="controls" style={{ marginBottom: 12 }}>
        <span className="badge b-good">
          {compliance.agent_deferred_total} deferred, not dropped
        </span>
        <span className="badge b-danger">
          {compliance.baseline_violation_total} baseline violations
        </span>
      </div>

      <div className="sub-h">Agent deferred to a legal window</div>
      <RuleList rules={compliance.agent_deferred} />

      <div className="sub-h">Agent blocked outright</div>
      <RuleList rules={compliance.agent_blocked} />

      <div className="sub-h">Baseline would have violated</div>
      <RuleList rules={compliance.baseline_would_have_violated} />

      <div className="chart-note">
        Deferrals keep the revenue and the rule: a quiet-hours message goes out at
        09:00 rather than being cancelled.
      </div>
    </div>
  );
}
