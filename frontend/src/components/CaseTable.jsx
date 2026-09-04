import { useMemo, useState } from "react";
import { IconTable } from "./Icons";
import { inr, STATUS_TONE, titleise } from "../lib/format";

const OUTCOMES = ["recovered", "escalated", "exhausted", "suppressed"];

const DX_TONE = { rule_engine: "b-muted", llm: "b-brand" };
const DX_LABEL = { rule_engine: "rule", llm: "LLM" };

export default function CaseTable({ events, causes, onOpen }) {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [cause, setCause] = useState("");

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return events.filter(
      (e) =>
        (!status || e.status === status) &&
        (!cause || e.root_cause === cause) &&
        (!needle ||
          (e.customer_name ?? "").toLowerCase().includes(needle) ||
          (e.customer_id ?? "").toLowerCase().includes(needle) ||
          (e.external_ref ?? "").toLowerCase().includes(needle))
    );
  }, [events, q, status, cause]);

  return (
    <div className="panel">
      <h2><IconTable size={15} />Cases — click any row for the full decision trail</h2>

      <div className="legend" style={{ marginBottom: 12 }}>
        <input
          type="search"
          placeholder="Search customer or reference…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ minWidth: 230 }}
          aria-label="Search cases"
        />
        <select value={status} onChange={(e) => setStatus(e.target.value)}
                aria-label="Filter by outcome" title="Filter by outcome">
          <option value="">All outcomes</option>
          {OUTCOMES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={cause} onChange={(e) => setCause(e.target.value)}
                aria-label="Filter by root cause" title="Filter by root cause">
          <option value="">All root causes</option>
          {(causes ?? []).map((c) => (
            <option key={c.root_cause} value={c.root_cause}>{titleise(c.root_cause)}</option>
          ))}
        </select>
        <span style={{ color: "var(--muted)", fontSize: 12 }}>
          {rows.length} of {events.length} cases
        </span>
      </div>

      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Case</th>
              <th>Type</th>
              <th className="num">Amount</th>
              <th>Root cause</th>
              <th>Dx</th>
              <th>Agent</th>
              <th>Baseline</th>
              <th className="num">Att.</th>
              <th className="num">Msgs</th>
              <th className="num">Recovered</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 300).map((e) => (
              <tr key={e.id} onClick={() => onOpen(e.id)}>
                <td>
                  <div>{e.customer_name || e.customer_id}</div>
                  <div style={{ color: "var(--muted)", fontSize: 11.5 }}>{e.external_ref}</div>
                </td>
                <td style={{ color: "var(--text-2)" }}>{titleise(e.event_type)}</td>
                <td className="num">{inr(e.amount)}</td>
                <td style={{ color: "var(--text-2)" }}>{e.root_cause_label ?? "—"}</td>
                <td>
                  <span className={`badge ${DX_TONE[e.diagnosis_source] ?? "b-warning"}`}>
                    {DX_LABEL[e.diagnosis_source] ?? "none"}
                  </span>
                </td>
                <td>
                  <span className={`badge ${STATUS_TONE[e.status] ?? "b-muted"}`}>{e.status}</span>
                </td>
                <td>
                  <span className={`badge ${STATUS_TONE[e.counterpart_status] ?? "b-muted"}`}
                        style={{ opacity: 0.72 }}>
                    {e.counterpart_status ?? "—"}
                  </span>
                </td>
                <td className="num">{e.attempts}</td>
                <td className="num">{e.contacts}</td>
                <td className="num"
                    style={e.amount_recovered > 0
                      ? { color: "var(--good)", fontWeight: 650 }
                      : { color: "var(--muted)" }}>
                  {e.amount_recovered > 0 ? inr(e.amount_recovered) : "—"}
                </td>
              </tr>
            ))}
            {!rows.length && (
              <tr><td colSpan={10} className="empty">No cases match.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
