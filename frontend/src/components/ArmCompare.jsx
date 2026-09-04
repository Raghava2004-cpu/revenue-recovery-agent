import ArmLegend from "./ArmLegend";
import { IconScale } from "./Icons";
import { inrShort } from "../lib/format";

function Row({ label, agentText, baselineText, agentValue, baselineValue, max }) {
  const pct = (v) => `${Math.min(100, (v / (max || 1)) * 100)}%`;
  return (
    <div className="cmp">
      <div className="cmp-top">
        <span className="k">{label}</span>
        <span className="v">
          <span style={{ color: "var(--agent)" }}>{agentText}</span>
          <span style={{ color: "var(--muted)" }}> vs </span>
          <span style={{ color: "var(--baseline)" }}>{baselineText}</span>
        </span>
      </div>
      <div className="track">
        <div className="fill" style={{ width: pct(agentValue), background: "var(--agent)" }} />
      </div>
      <div className="track" style={{ marginTop: 3 }}>
        <div className="fill" style={{ width: pct(baselineValue), background: "var(--baseline)" }} />
      </div>
    </div>
  );
}

export default function ArmCompare({ agent, baseline }) {
  const rows = [
    {
      label: "Recovery rate",
      agentText: `${agent.recovery_rate_pct}%`,
      baselineText: `${baseline.recovery_rate_pct}%`,
      agentValue: agent.recovery_rate_pct,
      baselineValue: baseline.recovery_rate_pct,
      max: 100,
    },
    {
      label: "Revenue recovered",
      agentText: inrShort(agent.amount_recovered_gross),
      baselineText: inrShort(baseline.amount_recovered_gross),
      agentValue: agent.amount_recovered_gross,
      baselineValue: baseline.amount_recovered_gross,
      max: agent.amount_at_risk,
    },
    {
      label: "Messages sent to customers",
      agentText: agent.contacts,
      baselineText: baseline.contacts,
      agentValue: agent.contacts,
      baselineValue: baseline.contacts,
      max: Math.max(agent.contacts, baseline.contacts),
    },
    {
      label: "Total attempts",
      agentText: agent.attempts,
      baselineText: baseline.attempts,
      agentValue: agent.attempts,
      baselineValue: baseline.attempts,
      max: Math.max(agent.attempts, baseline.attempts),
    },
  ];

  return (
    <div className="panel">
      <h2><IconScale size={15} />Agent vs. naive dunning baseline</h2>
      <ArmLegend />
      {rows.map((r) => <Row key={r.label} {...r} />)}
      <div className="chart-note">
        Fewer messages is the better number on rows three and four — the agent
        recovers more while contacting people less.
      </div>
    </div>
  );
}
