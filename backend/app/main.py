"""
AI Revenue Recovery Agent — HTTP surface.

    uvicorn app.main:app --reload --port 8000

Three entrypoints:
  POST /webhooks/razorpay   live — signature-verified Razorpay events
  POST /batch/run           demo — synthetic cases through both policy arms
  GET  /dashboard/*         read models for the UI
"""
import json
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

load_dotenv()

from app import audit, razorpay_client                      # noqa: E402
from app import taxonomy as tx                              # noqa: E402
from app.config import POLICY_VERSION                       # noqa: E402
from app.db import get_db                                   # noqa: E402
from app.metrics import compute_metrics                     # noqa: E402
from app.models import (AuditLog, Promise, RecoveryAction,   # noqa: E402
                        RevenueEvent, init_db)
from app.pipeline import orchestrator                       # noqa: E402
from app.policy import compliance, stopping                 # noqa: E402
from app.policy.playbooks import playbook_for               # noqa: E402
from app.sim.generator import generate_batch                # noqa: E402

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="AI Revenue Recovery Agent",
    description="Detect → Diagnose → Decide → Act, with stopping rules, "
                "compliance guardrails and a hash-chained audit trail.",
    version=POLICY_VERSION,
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

init_db()


# =============================================================================
# 1. Live: Razorpay webhooks
# =============================================================================

RAZORPAY_EVENT_MAP = {
    "payment.failed": tx.PAYMENT_FAILED,
    "payment_link.expired": tx.CHECKOUT_ABANDONED,
    "subscription.charged.failed": tx.SUBSCRIPTION_FAILED,
    "subscription.pending": tx.SUBSCRIPTION_FAILED,
    "invoice.expired": tx.INVOICE_OVERDUE,
}


@app.post("/webhooks/razorpay", tags=["live"])
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(None),
    db: Session = Depends(get_db),
):
    body = await request.body()

    try:
        valid = razorpay_client.verify_webhook_signature(body, x_razorpay_signature or "")
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not valid:
        # Logged as a security event, not silently dropped.
        audit.record(db, stage="detect",
                     decision="Rejected a webhook with an invalid signature.",
                     detail={"signature_present": bool(x_razorpay_signature)})
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    payload = json.loads(body)
    event_name = payload.get("event")
    event_type = RAZORPAY_EVENT_MAP.get(event_name)
    entities = payload.get("payload", {})

    # A successful payment on a link we generated closes the case it belongs to.
    if event_name in ("payment_link.paid", "payment.captured", "order.paid"):
        return _settle_from_webhook(db, entities)

    if event_type is None:
        return {"status": "ignored", "event": event_name}

    entity = (entities.get("payment", {}).get("entity")
              or entities.get("payment_link", {}).get("entity")
              or entities.get("subscription", {}).get("entity")
              or entities.get("invoice", {}).get("entity") or {})
    error = entity.get("error") or {}

    raw = {
        "case_key": entity.get("id") or f"live-{datetime.now(timezone.utc).timestamp()}",
        "external_ref": entity.get("id"),
        "event_type": event_type,
        "amount": (entity.get("amount") or 0) / 100.0,
        "payment_method": entity.get("method"),
        "raw_error_code": entity.get("error_code") or error.get("code"),
        "raw_error_reason": entity.get("error_reason") or error.get("reason"),
        "raw_error_description": entity.get("error_description") or error.get("description"),
        "customer_id": entity.get("customer_id") or entity.get("email") or "unknown",
        "customer_name": (entity.get("customer") or {}).get("name"),
        "customer_contact": entity.get("contact"),
        "customer_email": entity.get("email"),
        "segment": "returning",
        "language": "hinglish",
        # Live customers have no simulated ground truth; the outcome of a real
        # link is decided by a real payment, not by the model.
        "sim_propensity": None, "sim_funds_at": None, "sim_reachable": True,
    }

    event = orchestrator.run_live_event(db, raw, use_live_razorpay=True)
    return {
        "status": "processed", "event_id": event.id, "root_cause": event.root_cause,
        "diagnosis_source": event.diagnosis_source, "case_status": event.status,
        "next_action_at": event.next_action_at,
    }


def _settle_from_webhook(db: Session, entities: dict) -> dict:
    """Close the case whose recovery link was just paid."""
    entity = (entities.get("payment_link", {}).get("entity")
              or entities.get("payment", {}).get("entity") or {})
    notes = entity.get("notes") or {}
    case_key = notes.get("case_key")
    if not case_key:
        return {"status": "ignored", "reason": "no case_key in notes"}

    event = (db.query(RevenueEvent)
             .filter(RevenueEvent.case_key == case_key, RevenueEvent.arm == "agent")
             .first())
    if not event or event.status == "recovered":
        return {"status": "ignored", "reason": "no open case for this key"}

    now = datetime.now(timezone.utc)
    amount = (entity.get("amount_paid") or entity.get("amount") or 0) / 100.0 or event.amount
    event.status = "recovered"
    event.resolved_at = now
    event.amount_recovered = amount
    event.next_action_at = None

    last = (db.query(RecoveryAction)
            .filter(RecoveryAction.event_id == event.id)
            .order_by(RecoveryAction.attempt_no.desc()).first())
    if last:
        last.outcome = "recovered"
        last.amount_recovered = amount
    db.commit()

    audit.record(db, event_id=event.id, stage="observe", occurred_at=now,
                 decision=f"Recovery confirmed by webhook: ₹{amount:,.2f} paid on the "
                          f"generated link. Case closed as recovered.",
                 detail={"case_key": case_key, "amount": amount})
    return {"status": "recovered", "event_id": event.id, "amount": amount}


# =============================================================================
# 2. Demo: batch runner
# =============================================================================

@app.post("/batch/run", tags=["demo"])
def batch_run(
    # 250 rather than 60: at 60 cases the rupee lift is real but its confidence
    # interval still spans zero, because a handful of large B2B invoices
    # dominate the total. 250 puts the interval clear of zero in ~16s.
    n: int = Query(250, ge=1, le=1000),
    seed: int | None = 42,
    horizon_days: int = Query(14, ge=1, le=60),
    db: Session = Depends(get_db),
):
    """
    Generate n at-risk cases and run each one through BOTH policy arms.

    Deterministic for a given (n, seed): the same batch reproduces the same
    numbers, which is what makes the reported lift checkable rather than a
    number that moved because the dice moved.
    """
    cases = generate_batch(n=n, seed=seed)
    summary = orchestrator.run_batch(db, cases, horizon_days=horizon_days)
    return {"status": "completed", **summary}


@app.post("/batch/reset", tags=["demo"])
def batch_reset(db: Session = Depends(get_db)):
    for model in (Promise, RecoveryAction, AuditLog, RevenueEvent):
        db.query(model).delete()
    db.commit()
    return {"status": "reset"}


@app.post("/recovery/tick", tags=["live"])
def recovery_tick(db: Session = Depends(get_db)):
    """
    Advance every live case whose next action is due.

    In production a scheduler calls this on an interval; it's what turns the
    single webhook-time action into a real multi-step recovery journey.
    """
    return orchestrator.tick(db)


# =============================================================================
# 3. Dashboard
# =============================================================================

@app.get("/dashboard/metrics", tags=["dashboard"])
def dashboard_metrics(db: Session = Depends(get_db)):
    return compute_metrics(db)


@app.get("/dashboard/events", tags=["dashboard"])
def dashboard_events(
    arm: str = "agent",
    status: str | None = None,
    root_cause: str | None = None,
    q: str | None = None,
    limit: int = Query(200, le=1000),
    db: Session = Depends(get_db),
):
    query = db.query(RevenueEvent).filter(RevenueEvent.arm == arm)
    if status:
        query = query.filter(RevenueEvent.status == status)
    if root_cause:
        query = query.filter(RevenueEvent.root_cause == root_cause)
    if q:
        like = f"%{q}%"
        query = query.filter(
            (RevenueEvent.customer_id.like(like))
            | (RevenueEvent.external_ref.like(like))
            | (RevenueEvent.customer_name.like(like))
        )

    events = query.order_by(RevenueEvent.amount.desc()).limit(limit).all()
    twins = {
        e.case_key: e.status
        for e in db.query(RevenueEvent)
        .filter(RevenueEvent.arm == ("baseline" if arm == "agent" else "agent")).all()
    }

    return [{
        "id": e.id,
        "case_key": e.case_key,
        "external_ref": e.external_ref,
        "event_type": e.event_type,
        "customer_id": e.customer_id,
        "customer_name": e.customer_name,
        "segment": e.segment,
        "amount": e.amount,
        "root_cause": e.root_cause,
        "root_cause_label": tx.root_cause(e.root_cause).label,
        "diagnosis_source": e.diagnosis_source,
        "diagnosis_confidence": e.diagnosis_confidence,
        "status": e.status,
        "counterpart_status": twins.get(e.case_key),
        "attempts": e.attempt_count,
        "contacts": e.contact_count,
        "cost": round(e.cost_incurred or 0.0, 2),
        "amount_recovered": round(e.amount_recovered or 0.0, 2),
        "detected_at": e.detected_at,
        "resolved_at": e.resolved_at,
        "hours_to_resolve": round(
            (e.resolved_at - e.detected_at).total_seconds() / 3600, 1
        ) if e.resolved_at and e.detected_at else None,
    } for e in events]


@app.get("/dashboard/events/{event_id}/trail", tags=["dashboard"])
def event_trail(event_id: int, db: Session = Depends(get_db)):
    """
    Everything that happened to one case, in both arms, with the reasoning.

    This is the view that answers "why did the agent do that" — playbook,
    policy version, compliance verdicts, the outcome model's factor breakdown,
    and the paired baseline journey for the same customer.
    """
    event = db.get(RevenueEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    cause = tx.root_cause(event.root_cause)
    playbook = playbook_for(event.root_cause, event.arm)

    logs = (db.query(AuditLog).filter(AuditLog.event_id == event_id)
            .order_by(AuditLog.seq).all())
    actions = (db.query(RecoveryAction).filter(RecoveryAction.event_id == event_id)
               .order_by(RecoveryAction.attempt_no).all())
    promises = db.query(Promise).filter(Promise.event_id == event_id).all()

    twin = (db.query(RevenueEvent)
            .filter(RevenueEvent.case_key == event.case_key,
                    RevenueEvent.arm != event.arm).first())

    return {
        "event": {
            "id": event.id, "case_key": event.case_key, "arm": event.arm,
            "external_ref": event.external_ref, "event_type": event.event_type,
            "amount": event.amount, "customer_id": event.customer_id,
            "customer_name": event.customer_name, "segment": event.segment,
            "language": event.language, "status": event.status,
            "attempts": event.attempt_count, "contacts": event.contact_count,
            "cost": round(event.cost_incurred or 0.0, 2),
            "amount_recovered": round(event.amount_recovered or 0.0, 2),
            "detected_at": event.detected_at, "resolved_at": event.resolved_at,
            "policy_version": event.policy_version,
            "consent": {"whatsapp": event.consent_whatsapp, "sms": event.consent_sms,
                        "voice": event.consent_voice, "dnc": event.do_not_contact},
            "signal": {"code": event.raw_error_code, "reason": event.raw_error_reason,
                       "description": event.raw_error_description,
                       "method": event.payment_method},
        },
        "diagnosis": {
            "root_cause": event.root_cause, "label": cause.label,
            "source": event.diagnosis_source,
            "confidence": event.diagnosis_confidence,
            "rationale": event.diagnosis_rationale,
            "physics": cause.note,
            "auto_retry_safe": cause.auto_retry_safe,
            "needs_new_instrument": cause.needs_new_instrument,
            "hard_stop": cause.hard_stop,
        },
        "playbook": {
            "rationale": playbook.rationale,
            "max_contacts": playbook.max_contacts,
            "steps": [{
                "n": i + 1, "action": s.action, "channel": s.channel,
                "delay": _humanise(s.delay), "timing": s.timing, "note": s.note,
                "state": ("done" if i < event.attempt_count else "pending"),
            } for i, s in enumerate(playbook.steps)],
        },
        "audit_trail": [{
            "seq": l.seq, "stage": l.stage, "decision": l.decision,
            "detail": json.loads(l.detail) if l.detail else None,
            "at": l.occurred_at, "policy_version": l.policy_version,
            "entry_hash": l.entry_hash[:12] if l.entry_hash else None,
        } for l in logs],
        "actions": [{
            "attempt_no": a.attempt_no, "action_type": a.action_type,
            "channel": a.channel, "message": a.message,
            "message_source": a.message_source, "outcome": a.outcome,
            "amount_recovered": a.amount_recovered, "cost": a.cost,
            "success_probability": a.success_probability,
            "payment_link_url": a.payment_link_url, "at": a.executed_at,
        } for a in actions],
        "promises": [{
            "amount": p.amount, "promised_for": p.promised_for,
            "status": p.status, "settled_at": p.settled_at,
        } for p in promises],
        "counterpart": {
            "id": twin.id, "arm": twin.arm, "status": twin.status,
            "attempts": twin.attempt_count, "contacts": twin.contact_count,
            "cost": round(twin.cost_incurred or 0.0, 2),
            "amount_recovered": round(twin.amount_recovered or 0.0, 2),
            "actions": [{
                "attempt_no": a.attempt_no, "action_type": a.action_type,
                "channel": a.channel, "outcome": a.outcome,
                "success_probability": a.success_probability, "at": a.executed_at,
            } for a in sorted(twin.actions, key=lambda x: x.attempt_no)],
        } if twin else None,
    }


@app.get("/dashboard/timeline", tags=["dashboard"])
def timeline(db: Session = Depends(get_db)):
    """Cumulative recovered amount per arm over the simulated horizon — the
    chart that shows the two policies diverging."""
    rows = (db.query(RevenueEvent.arm, RevenueEvent.resolved_at,
                     RevenueEvent.amount_recovered)
            .filter(RevenueEvent.status == "recovered",
                    RevenueEvent.resolved_at.isnot(None))
            .order_by(RevenueEvent.resolved_at).all())
    if not rows:
        return {"points": []}

    start = min(r.resolved_at for r in rows)
    running = {"agent": 0.0, "baseline": 0.0}
    points = []
    for arm, at, amount in rows:
        running[arm] = running.get(arm, 0.0) + (amount or 0.0)
        points.append({
            "hours": round((at - start).total_seconds() / 3600, 2),
            "at": at, "arm": arm,
            "agent_total": round(running["agent"], 2),
            "baseline_total": round(running["baseline"], 2),
        })
    return {"points": points}


@app.get("/dashboard/policy", tags=["dashboard"])
def policy_view():
    """The whole policy, served as data — what the agent may do and when."""
    from app.policy.playbooks import PLAYBOOKS, BASELINE_PLAYBOOK
    return {
        "policy_version": POLICY_VERSION,
        "playbooks": {
            code: {
                "rationale": pb.rationale, "max_contacts": pb.max_contacts,
                "steps": [{"action": s.action, "channel": s.channel,
                           "delay": _humanise(s.delay), "timing": s.timing,
                           "note": s.note} for s in pb.steps],
            } for code, pb in PLAYBOOKS.items()
        },
        "baseline": {
            "rationale": BASELINE_PLAYBOOK.rationale,
            "steps": [{"action": s.action, "channel": s.channel,
                       "delay": _humanise(s.delay)} for s in BASELINE_PLAYBOOK.steps],
        },
        "compliance": {
            "quiet_hours_ist": f"{compliance.QUIET_START_HOUR}:00–{compliance.QUIET_END_HOUR}:00",
            "voice_window_ist": f"{compliance.VOICE_START_HOUR}:00–{compliance.VOICE_END_HOUR}:00",
            "max_contacts_per_7_days": compliance.MAX_CONTACTS_PER_7_DAYS,
            "min_gap_hours": compliance.MIN_GAP_BETWEEN_CONTACTS.total_seconds() / 3600,
            "human_approval_threshold_inr": compliance.HUMAN_APPROVAL_THRESHOLD_INR,
        },
        "stopping": {
            "cost_ceiling_ratio": stopping.COST_CEILING_RATIO,
            "max_case_age_days": {k: v.days for k, v in stopping.MAX_CASE_AGE.items()},
        },
        "root_causes": [{
            "code": c.code, "label": c.label, "family": c.family,
            "auto_retry_safe": c.auto_retry_safe,
            "needs_new_instrument": c.needs_new_instrument,
            "hard_stop": c.hard_stop, "note": c.note,
        } for c in tx.ROOT_CAUSES.values()],
    }


@app.get("/audit/verify", tags=["audit"])
def audit_verify(db: Session = Depends(get_db)):
    """Recompute the entire hash chain and report the first divergence, if any."""
    return audit.verify_chain(db)


@app.get("/audit/log", tags=["audit"])
def audit_log(limit: int = Query(200, le=2000), offset: int = 0,
              db: Session = Depends(get_db)):
    total = db.query(func.count(AuditLog.id)).scalar() or 0
    rows = (db.query(AuditLog).order_by(AuditLog.seq)
            .offset(offset).limit(limit).all())
    return {
        "total": total,
        "entries": [{
            "seq": r.seq, "stage": r.stage, "event_id": r.event_id,
            "decision": r.decision, "at": r.occurred_at,
            "policy_version": r.policy_version,
            "prev_hash": (r.prev_hash or "")[:12],
            "entry_hash": (r.entry_hash or "")[:12],
        } for r in rows],
    }


@app.get("/health", tags=["ops"])
def health(db: Session = Depends(get_db)):
    from app.ai import client as ai_client
    return {
        "status": "ok",
        "policy_version": POLICY_VERSION,
        "events": db.query(func.count(RevenueEvent.id)).scalar() or 0,
        "llm": ai_client.usage.snapshot(),
        "razorpay_configured": bool(razorpay_client.RAZORPAY_KEY_ID),
    }


def _humanise(delta) -> str:
    secs = delta.total_seconds()
    if secs == 0:
        return "immediate"
    if secs < 3600:
        return f"{secs / 60:.0f}m"
    if secs < 86400:
        return f"{secs / 3600:.0f}h"
    return f"{secs / 86400:.0f}d"
