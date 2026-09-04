import { IconAct, IconAlert, IconRupee, IconUsers } from "./Icons";
import { inrShort } from "../lib/format";

function Tile({ icon: Icon, label, value, foot }) {
  return (
    <div className="panel tile">
      <div className="label"><Icon size={15} />{label}</div>
      <div className="value">{value}</div>
      <div className="foot">{foot}</div>
    </div>
  );
}

export default function StatTiles({ agent, lift }) {
  const saved = lift?.contacts_saved ?? 0;
  return (
    <div className="grid g-4">
      <Tile
        icon={IconRupee}
        label="Recovered by agent"
        value={inrShort(agent.amount_recovered_gross)}
        foot={`${agent.recovered_count} of ${agent.events} cases · net ${inrShort(
          agent.amount_recovered_net
        )} after ₹${agent.cost_incurred.toFixed(0)} of contact spend`}
      />
      <Tile
        icon={IconAlert}
        label="Still at risk"
        value={inrShort(agent.amount_at_risk - agent.amount_recovered_gross)}
        foot={`of ${inrShort(agent.amount_at_risk)} detected`}
      />
      <Tile
        icon={IconUsers}
        label="Handed to humans"
        value={inrShort(agent.amount_handed_off)}
        foot={`${agent.escalated_count} escalated + ${agent.suppressed_count} suppressed · counted as NOT recovered`}
      />
      <Tile
        icon={IconAct}
        label="Customer contacts"
        value={agent.contacts.toLocaleString()}
        foot={`${saved >= 0 ? `${saved} fewer` : `${-saved} more`} than baseline · ${
          agent.contacts_per_recovery ?? "—"
        } per recovery`}
      />
    </div>
  );
}
