"""
Run a batch and print the results, without the API or the dashboard.

    python seed.py                 # 250 cases, the default demo
    python seed.py --n 500 --seed 7

Useful for checking that a policy change actually improved anything before
touching the UI.
"""
import argparse
import sys

from dotenv import load_dotenv

# The Windows console defaults to cp1252, which cannot encode "₹" — every line
# of this report would raise UnicodeEncodeError. Force UTF-8 on the streams.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

load_dotenv()

from app.db import SessionLocal            # noqa: E402
from app.metrics import compute_metrics    # noqa: E402
from app.models import init_db             # noqa: E402
from app.pipeline import orchestrator      # noqa: E402
from app.sim.generator import generate_batch  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=250)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--horizon", type=int, default=14)
    ap.add_argument("--reset", action="store_true", help="wipe existing data first")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()

    if args.reset:
        from app.models import AuditLog, Promise, RecoveryAction, RevenueEvent
        for model in (Promise, RecoveryAction, AuditLog, RevenueEvent):
            db.query(model).delete()
        db.commit()

    summary = orchestrator.run_batch(
        db, generate_batch(n=args.n, seed=args.seed), horizon_days=args.horizon
    )
    m = compute_metrics(db)
    a, b, lift, sig = m["agent"], m["baseline"], m["lift"], m["significance"]

    print(f"\n  {args.n} cases × 2 policy arms · {args.horizon}-day horizon · "
          f"policy {summary['policy_version']}")
    print(f"  {summary['scheduled_actions_processed']} scheduled actions processed\n")

    print(f"  {'':30s}{'AGENT':>16s}{'BASELINE':>16s}")
    rows = [
        ("Recovery rate", f"{a['recovery_rate_pct']}%", f"{b['recovery_rate_pct']}%"),
        ("Revenue recovered", f"₹{a['amount_recovered_gross']:,.0f}",
         f"₹{b['amount_recovered_gross']:,.0f}"),
        ("Net of contact cost", f"₹{a['amount_recovered_net']:,.0f}",
         f"₹{b['amount_recovered_net']:,.0f}"),
        ("Messages sent", a["contacts"], b["contacts"]),
        ("Total attempts", a["attempts"], b["attempts"]),
        ("Escalated to humans", a["escalated_count"], b["escalated_count"]),
    ]
    for label, av, bv in rows:
        print(f"  {label:30s}{str(av):>16s}{str(bv):>16s}")

    print(f"\n  Incremental revenue    ₹{lift['incremental_amount_gross']:,.0f}"
          f"   (90% CI ₹{sig['ci90_low']:,.0f} to ₹{sig['ci90_high']:,.0f})")
    print(f"  Cases won / lost       {sig['cases_agent_won']} / {sig['cases_agent_lost']}"
          f"   {'SIGNIFICANT' if sig['significant'] else 'not significant at this n'}")

    print("\n  Incremental ₹ by root cause")
    for r in m["by_root_cause"]:
        if r["count"]:
            print(f"    {r['root_cause']:36s} n={r['count']:<4d} "
                  f"{r['agent_rate_pct']:>5.1f}% vs {r['baseline_rate_pct']:>5.1f}%  "
                  f"₹{r['incremental_amount']:>12,.0f}")

    c = m["compliance"]
    print(f"\n  Compliance: agent deferred {c['agent_deferred_total']}, "
          f"blocked {c['agent_blocked_total']}; "
          f"baseline would have violated {c['baseline_violation_total']} rules")
    d = m["diagnosis"]
    print(f"  Diagnosis:  {d['rule_engine_pct']}% rule engine, {d['llm_pct']}% LLM, "
          f"{d['unclassified_pct']}% to humans")

    from app import audit
    print(f"  Audit:      {audit.verify_chain(db)['message']}\n")
    db.close()


if __name__ == "__main__":
    main()
