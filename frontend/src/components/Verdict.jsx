import { inrShort } from "../lib/format";

/**
 * The headline: incremental revenue, with its confidence interval.
 *
 * The interval is shown at the same visual weight as the point estimate on
 * purpose. A single batch's rupee total is dominated by a handful of large B2B
 * invoices, so quoting the point estimate alone would overstate what one run
 * actually establishes.
 */
export default function Verdict({ lift, significance }) {
  const delta = lift?.incremental_amount_gross ?? 0;
  const positive = delta >= 0;
  const sig = significance ?? {};

  return (
    <div className="panel verdict">
      <div className="cap">Incremental revenue recovered vs. baseline policy</div>
      <div className={`hero ${positive ? "pos" : "neg"}`}>{inrShort(delta)}</div>

      {sig.ci90_low !== undefined && (
        <div className="ci">
          90% confidence interval {inrShort(sig.ci90_low)} to {inrShort(sig.ci90_high)}
        </div>
      )}

      <div className="controls" style={{ marginTop: 13 }}>
        <span className={`badge ${sig.significant ? "b-good" : "b-warning"}`}>
          {sig.significant
            ? "statistically significant"
            : "not significant at this batch size"}
        </span>
        <span className="badge b-muted">
          {sig.cases_agent_won ?? 0} cases won · {sig.cases_agent_lost ?? 0} lost
        </span>
        <span className="badge b-muted">
          +{lift?.recovery_rate_delta_pp ?? 0} pp recovery rate
        </span>
      </div>

      {sig.interpretation && <div className="note">{sig.interpretation}</div>}

      <div className="chart-note" style={{ marginTop: 14 }}>
        A recovery rate on its own has no counterfactual in it. Every case is run
        twice — once under the agent's policy, once under a naive dunning baseline —
        against the <b>same customers</b> drawing the <b>same random numbers</b>, so
        the difference between the two is attributable to policy rather than luck.
        {sig.paired_cases ? ` Paired over ${sig.paired_cases} cases.` : ""}
      </div>
    </div>
  );
}
