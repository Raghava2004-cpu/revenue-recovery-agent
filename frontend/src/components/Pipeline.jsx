import {
  IconAct, IconArrow, IconDecide, IconDetect, IconDiagnose, IconObserve,
} from "./Icons";
import { inrShort } from "../lib/format";

/**
 * The "what does this thing actually do" panel.
 *
 * A reviewer landing on this dashboard has about fifteen seconds to work out
 * what the agent is before the numbers mean anything. So the five pipeline
 * stages are shown as a strip, each carrying its real count from the run that
 * just happened — the diagram is the architecture *and* the throughput, rather
 * than a static picture that could be describing anything.
 */
export default function Pipeline({ metrics, onWalkthrough }) {
  const a = metrics.agent;
  const dx = metrics.diagnosis;
  const c = metrics.compliance;

  const stages = [
    {
      icon: IconDetect,
      name: "Detect",
      value: a.events.toLocaleString(),
      unit: "cases at risk",
      body: `Razorpay webhooks and batch cases normalise into one shape — ${inrShort(
        a.amount_at_risk
      )} of failed payments, abandoned checkouts, bounced mandates and overdue invoices.`,
    },
    {
      icon: IconDiagnose,
      name: "Diagnose",
      value: `${dx.rule_engine_pct}%`,
      unit: "by rules, not a model",
      body: `A deterministic rule engine resolves the error reason. The LLM is asked only for the free-text tail it can't match; below 70% confidence the case goes to a human rather than a guess.`,
    },
    {
      icon: IconDecide,
      name: "Decide",
      value: a.attempts.toLocaleString(),
      unit: "decisions taken",
      body: `Stopping rules → playbook → compliance, in that order. Each root cause has its own ladder, escalating by cost: free silent retry first, a human last.`,
    },
    {
      icon: IconAct,
      name: "Act",
      value: a.contacts.toLocaleString(),
      unit: "messages sent",
      body: `Payment links, mandate retries and Hinglish messages, on a schedule. ${c.agent_deferred_total} actions were deferred to a legal window instead of being dropped.`,
    },
    {
      icon: IconObserve,
      name: "Observe",
      value: inrShort(a.amount_recovered_gross),
      unit: "recovered",
      body: `Outcomes close the loop. ${a.escalated_count} cases the agent refused to resolve alone went to humans, and are reported as unrecovered.`,
    },
  ];

  return (
    <section className="panel pipeline-panel">
      <div className="pipeline-head">
        <div>
          <h2 style={{ marginBottom: 6 }}>What this agent does</h2>
          <p className="pipeline-lede">
            It finds revenue that failed, works out <em>why</em> it failed, picks a
            bounded intervention, runs it on a schedule, and stops when continuing
            would cost more than it recovers — writing every decision to an audit
            trail you can verify.
          </p>
        </div>
        {onWalkthrough && (
          <button type="button" className="primary" onClick={onWalkthrough}>
            Walk through one case
          </button>
        )}
      </div>

      <ol className="stages">
        {stages.map((s, i) => (
          <li className="stage" key={s.name}>
            <div className="stage-card">
              <div className="stage-top">
                <span className="stage-icon"><s.icon size={17} /></span>
                <span className="stage-name">{s.name}</span>
              </div>
              <div className="stage-value">{s.value}</div>
              <div className="stage-unit">{s.unit}</div>
              <p className="stage-body">{s.body}</p>
            </div>
            {i < stages.length - 1 && (
              <span className="stage-arrow" aria-hidden="true"><IconArrow /></span>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
