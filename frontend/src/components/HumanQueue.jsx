import { IconUsers } from "./Icons";
import { inrShort } from "../lib/format";

export default function HumanQueue({ exceptions, onOpen }) {
  const rows = (exceptions ?? []).slice(0, 12);

  return (
    <div className="panel">
      <h2><IconUsers size={15} />Human queue — refused to act alone</h2>

      {rows.length ? (
        rows.map((e) => (
          <div
            key={e.id}
            className="queue-item"
            role="button"
            tabIndex={0}
            onClick={() => onOpen(e.id)}
            onKeyDown={(ev) => ev.key === "Enter" && onOpen(e.id)}
          >
            <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
              <span style={{ color: "var(--text-2)", fontSize: 12.5 }}>
                {e.root_cause_label}
              </span>
              <b style={{ fontVariantNumeric: "tabular-nums" }}>{inrShort(e.amount)}</b>
            </div>
            <div style={{ color: "var(--muted)", fontSize: 11.5 }}>
              {e.why.slice(0, 92)}…
            </div>
          </div>
        ))
      ) : (
        <div style={{ color: "var(--muted)", fontSize: 12.5 }}>Nothing escalated.</div>
      )}

      <div className="chart-note">
        Every case here is reported as unrecovered, never as a success.
      </div>
    </div>
  );
}
