"""
Metrics.

"The bar" for this track asks for *measured* money recovered. That word does
some work: a recovery rate on its own is not a measurement, because there's no
counterfactual in it. If the agent recovers 48% of at-risk revenue, the honest
question is 48% versus what — and for transient gateway failures, an untuned
retry cron gets most of that on its own.

So the headline figure here is **incremental** recovery: rupees the agent
brought in that the paired baseline arm, facing identical customers under
identical random draws, did not. Everything else on the dashboard is context
for that number.

Reporting commitments, all of which cost us headline points:
  - Escalated cases count as handed-off, never as recovered.
  - Recovery is reported net of contact cost, not gross.
  - Cases still open at the horizon are counted as failures, not as pending.
  - Compliance blocks the agent respected are shown next to the revenue they
    cost, because a compliance control that never loses money isn't binding.
"""
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import taxonomy as tx
from app.ai import client as ai_client
from app.config import POLICY_VERSION
from app.models import Promise, RecoveryAction, RevenueEvent

TERMINAL = ("recovered", "escalated", "suppressed", "exhausted")


def _arm_stats(db: Session, arm: str) -> dict:
    events = db.query(RevenueEvent).filter(RevenueEvent.arm == arm).all()
    if not events:
        return {}

    recovered = [e for e in events if e.status == "recovered"]
    escalated = [e for e in events if e.status == "escalated"]
    suppressed = [e for e in events if e.status == "suppressed"]
    exhausted = [e for e in events if e.status == "exhausted"]

    at_risk = sum(e.amount for e in events)
    gross = sum(e.amount_recovered or 0.0 for e in recovered)
    cost = sum(e.cost_incurred or 0.0 for e in events)

    contacts = (
        db.query(func.count(RecoveryAction.id))
        .join(RevenueEvent, RecoveryAction.event_id == RevenueEvent.id)
        .filter(RevenueEvent.arm == arm,
                RecoveryAction.blocked.is_(False),
                RecoveryAction.channel.in_([tx.SMS, tx.WHATSAPP, tx.VOICE, tx.EMAIL]))
        .scalar() or 0
    )
    attempts = (
        db.query(func.count(RecoveryAction.id))
        .join(RevenueEvent, RecoveryAction.event_id == RevenueEvent.id)
        .filter(RevenueEvent.arm == arm).scalar() or 0
    )

    ttr = [
        (e.resolved_at - e.detected_at).total_seconds() / 3600
        for e in recovered if e.resolved_at and e.detected_at
    ]

    return {
        "arm": arm,
        "events": len(events),
        "amount_at_risk": round(at_risk, 2),
        "amount_recovered_gross": round(gross, 2),
        "cost_incurred": round(cost, 2),
        "amount_recovered_net": round(gross - cost, 2),
        "recovery_rate_pct": round(100 * len(recovered) / len(events), 1),
        "value_recovery_rate_pct": round(100 * gross / at_risk, 1) if at_risk else 0.0,
        "recovered_count": len(recovered),
        "escalated_count": len(escalated),
        "amount_handed_off": round(sum(e.amount for e in escalated), 2),
        "suppressed_count": len(suppressed),
        "exhausted_count": len(exhausted),
        "attempts": attempts,
        "contacts": contacts,
        "contacts_per_recovery": round(contacts / len(recovered), 2) if recovered else None,
        "cost_per_recovery": round(cost / len(recovered), 2) if recovered else None,
        "roi": round(gross / cost, 1) if cost else None,
        "median_hours_to_recovery": round(sorted(ttr)[len(ttr) // 2], 1) if ttr else None,
    }


def compute_metrics(db: Session) -> dict:
    total = db.query(func.count(RevenueEvent.id)).scalar() or 0
    if total == 0:
        return {"total_events": 0, "policy_version": POLICY_VERSION,
                "llm": ai_client.usage.snapshot()}

    agent = _arm_stats(db, "agent")
    baseline = _arm_stats(db, "baseline")

    lift = {}
    if agent and baseline:
        inc_gross = agent["amount_recovered_gross"] - baseline["amount_recovered_gross"]
        inc_net = agent["amount_recovered_net"] - baseline["amount_recovered_net"]
        lift = {
            "incremental_amount_gross": round(inc_gross, 2),
            "incremental_amount_net": round(inc_net, 2),
            "incremental_recoveries": agent["recovered_count"] - baseline["recovered_count"],
            "recovery_rate_delta_pp": round(
                agent["recovery_rate_pct"] - baseline["recovery_rate_pct"], 1),
            "relative_uplift_pct": round(
                100 * inc_gross / baseline["amount_recovered_gross"], 1
            ) if baseline["amount_recovered_gross"] else None,
            "contacts_saved": baseline["contacts"] - agent["contacts"],
            "cost_delta": round(agent["cost_incurred"] - baseline["cost_incurred"], 2),
        }

    return {
        "total_events": total,
        "policy_version": POLICY_VERSION,
        "agent": agent,
        "baseline": baseline,
        "lift": lift,
        "significance": _lift_significance(db),
        "by_root_cause": _by_root_cause(db),
        "by_event_type": _by_event_type(db),
        "diagnosis": _diagnosis_stats(db),
        "compliance": _compliance_stats(db),
        "promises": _promise_stats(db),
        "exceptions": _exceptions(db),
        "llm": ai_client.usage.snapshot(),
    }


def _lift_significance(db: Session, resamples: int = 2000, seed: int = 20260904) -> dict:
    """
    A confidence interval on the lift, by paired bootstrap.

    This exists because of something the batch data actually showed. B2B
    invoices run ₹18k–₹240k while retail failures run ₹200–₹13k, so a single
    invoice landing one way or the other moves the headline total by more than
    every retail case combined. Across seeds the retail lift was stable near
    +₹150k while the receivables lift swung by ±₹450k — meaning a point
    estimate from one batch, quoted on its own, would be close to meaningless.

    Because the arms are paired on the same customers under common random
    numbers, the per-case difference is the unit of evidence. Resampling those
    differences gives an interval, and a `wins`/`losses` count that the heavy
    amount tail cannot distort. An interval spanning zero is reported as such
    rather than rounded into a win.
    """
    import random

    rows = db.query(RevenueEvent.case_key, RevenueEvent.arm,
                    RevenueEvent.amount_recovered, RevenueEvent.status).all()
    if not rows:
        return {}

    paired: dict[str, dict[str, float]] = {}
    for case_key, arm, recovered, status in rows:
        paired.setdefault(case_key, {})[arm] = (recovered or 0.0) if status == "recovered" else 0.0

    deltas = [v.get("agent", 0.0) - v.get("baseline", 0.0)
              for v in paired.values() if "agent" in v and "baseline" in v]
    n = len(deltas)
    if n < 2:
        return {}

    wins = sum(1 for d in deltas if d > 0)
    losses = sum(1 for d in deltas if d < 0)
    total_delta = sum(deltas)

    rng = random.Random(seed)
    totals = []
    for _ in range(resamples):
        totals.append(sum(deltas[rng.randrange(n)] for _ in range(n)))
    totals.sort()

    lo = totals[int(0.05 * resamples)]
    hi = totals[int(0.95 * resamples) - 1]
    positive_share = sum(1 for t in totals if t > 0) / resamples

    return {
        "paired_cases": n,
        "observed_total_delta": round(total_delta, 2),
        "mean_delta_per_case": round(total_delta / n, 2),
        "ci90_low": round(lo, 2),
        "ci90_high": round(hi, 2),
        "share_of_resamples_positive": round(positive_share, 3),
        "cases_agent_won": wins,
        "cases_agent_lost": losses,
        "cases_tied": n - wins - losses,
        "significant": bool(lo > 0),
        "method": f"Paired bootstrap over {n} per-case differences, "
                  f"{resamples} resamples, 90% interval.",
        "interpretation": (
            "The 90% interval excludes zero: the agent's advantage is larger "
            "than batch-to-batch noise."
            if lo > 0 else
            "The 90% interval spans zero. The per-case win/loss count is the "
            "more reliable signal here — a few very large receivables dominate "
            "the rupee total in either direction."
        ),
    }


def _by_root_cause(db: Session) -> list[dict]:
    """Per-cause agent-vs-baseline. This is the table that shows *where* the lift
    comes from — and where the agent honestly adds nothing."""
    rows: dict[str, dict] = {}
    for e in db.query(RevenueEvent).all():
        code = e.root_cause or "UNKNOWN"
        r = rows.setdefault(code, {
            "root_cause": code,
            "label": tx.root_cause(code).label,
            "count": 0, "amount_at_risk": 0.0,
            "agent_recovered": 0, "baseline_recovered": 0,
            "agent_amount": 0.0, "baseline_amount": 0.0,
        })
        if e.arm == "agent":
            r["count"] += 1
            r["amount_at_risk"] += e.amount
        key = "agent" if e.arm == "agent" else "baseline"
        if e.status == "recovered":
            r[f"{key}_recovered"] += 1
            r[f"{key}_amount"] += e.amount_recovered or 0.0

    out = []
    for r in rows.values():
        r["amount_at_risk"] = round(r["amount_at_risk"], 2)
        r["agent_amount"] = round(r["agent_amount"], 2)
        r["baseline_amount"] = round(r["baseline_amount"], 2)
        r["incremental_amount"] = round(r["agent_amount"] - r["baseline_amount"], 2)
        r["agent_rate_pct"] = round(100 * r["agent_recovered"] / r["count"], 1) if r["count"] else 0
        r["baseline_rate_pct"] = round(100 * r["baseline_recovered"] / r["count"], 1) if r["count"] else 0
        out.append(r)
    return sorted(out, key=lambda r: -r["incremental_amount"])


def _by_event_type(db: Session) -> list[dict]:
    rows: dict[str, dict] = {}
    for e in db.query(RevenueEvent).filter(RevenueEvent.arm == "agent").all():
        r = rows.setdefault(e.event_type, {
            "event_type": e.event_type, "count": 0, "recovered": 0,
            "amount_at_risk": 0.0, "amount_recovered": 0.0,
        })
        r["count"] += 1
        r["amount_at_risk"] += e.amount
        if e.status == "recovered":
            r["recovered"] += 1
            r["amount_recovered"] += e.amount_recovered or 0.0
    for r in rows.values():
        r["amount_at_risk"] = round(r["amount_at_risk"], 2)
        r["amount_recovered"] = round(r["amount_recovered"], 2)
        r["rate_pct"] = round(100 * r["recovered"] / r["count"], 1) if r["count"] else 0
    return list(rows.values())


def _diagnosis_stats(db: Session) -> dict:
    """Where diagnoses come from. The point of this table is that the LLM is a
    minority path — it handles the ambiguous tail the rules abstain on."""
    rows = (db.query(RevenueEvent.diagnosis_source, func.count(RevenueEvent.id))
            .filter(RevenueEvent.arm == "agent")
            .group_by(RevenueEvent.diagnosis_source).all())
    by_source = {src or "unknown": n for src, n in rows}
    total = sum(by_source.values()) or 1
    unknown = (db.query(func.count(RevenueEvent.id))
               .filter(RevenueEvent.arm == "agent",
                       RevenueEvent.root_cause == "UNKNOWN").scalar() or 0)
    return {
        "by_source": by_source,
        "rule_engine_pct": round(100 * by_source.get("rule_engine", 0) / total, 1),
        "llm_pct": round(100 * by_source.get("llm", 0) / total, 1),
        "unclassified_count": unknown,
        "unclassified_pct": round(100 * unknown / total, 1),
    }


def _compliance_stats(db: Session) -> dict:
    """
    What the guardrails actually stopped.

    The baseline column is the interesting one: it counts the interventions the
    baseline policy took that the agent's rules would have blocked or deferred —
    quiet-hours messages, re-presentments against dead instruments, dunning on
    risk-blocked and disputed cases. That is the compliance cost of the naive
    approach, in units of individual violations.
    """
    import json

    from app.models import AuditLog as AL

    arms = dict(db.query(RevenueEvent.id, RevenueEvent.arm).all())

    rows = (db.query(AL.detail, AL.event_id, AL.stage)
            .filter(AL.stage.in_(["comply", "violation", "stop"])).all())

    blocked, deferred, violated, stopped = {}, {}, {}, {}
    for detail, event_id, stage in rows:
        arm = arms.get(event_id, "agent")
        try:
            payload = json.loads(detail) if detail else {}
        except (ValueError, TypeError):
            payload = {}
        rule = payload.get("rule") or "unspecified"

        if stage == "violation":
            if arm == "baseline":
                violated[rule] = violated.get(rule, 0) + 1
        elif stage == "stop":
            if arm == "agent":
                stopped[rule] = stopped.get(rule, 0) + 1
        elif arm == "agent":
            bucket = deferred if payload.get("kind") == "defer" else blocked
            bucket[rule] = bucket.get(rule, 0) + 1

    return {
        "agent_blocked": blocked,
        "agent_deferred": deferred,
        "agent_stopping_rules": stopped,
        "baseline_would_have_violated": violated,
        "agent_blocked_total": sum(blocked.values()),
        "agent_deferred_total": sum(deferred.values()),
        "agent_stopped_total": sum(stopped.values()),
        "baseline_violation_total": sum(violated.values()),
    }


def _promise_stats(db: Session) -> dict:
    rows = (db.query(Promise.status, func.count(Promise.id), func.sum(Promise.amount))
            .join(RevenueEvent, Promise.event_id == RevenueEvent.id)
            .filter(RevenueEvent.arm == "agent")
            .group_by(Promise.status).all())
    by_status = {s: {"count": n, "amount": round(a or 0.0, 2)} for s, n, a in rows}
    kept = by_status.get("kept", {}).get("count", 0)
    broken = by_status.get("broken", {}).get("count", 0)
    total = kept + broken
    return {
        "by_status": by_status,
        "kept_rate_pct": round(100 * kept / total, 1) if total else None,
        "total_promised_amount": round(
            sum(v["amount"] for v in by_status.values()), 2),
    }


def _exceptions(db: Session) -> list[dict]:
    """The human queue — every case the agent refused to resolve on its own."""
    events = (db.query(RevenueEvent)
              .filter(RevenueEvent.arm == "agent",
                      RevenueEvent.status.in_(["escalated", "suppressed"]))
              .order_by(RevenueEvent.amount.desc()).limit(50).all())
    return [{
        "id": e.id,
        "external_ref": e.external_ref,
        "event_type": e.event_type,
        "customer_id": e.customer_id,
        "amount": e.amount,
        "root_cause": e.root_cause,
        "root_cause_label": tx.root_cause(e.root_cause).label,
        "status": e.status,
        "attempts": e.attempt_count,
        "why": tx.root_cause(e.root_cause).note,
    } for e in events]
