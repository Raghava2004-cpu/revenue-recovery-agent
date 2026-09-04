"""
DECIDE — pick the next move, or decide there isn't one.

Three gates, in this order, and the order is load-bearing:

  1. Stopping rules  — is this case still worth working at all?
  2. Playbook        — what does policy say the next step is?
  3. Compliance      — are we allowed to take that step, right now?

Compliance runs last so that it can *defer* a specific, already-chosen action to
a legal time rather than vetoing the case. Running it first would only ever be
able to answer "no", and every quiet-hours case would become lost revenue
instead of a message that goes out at 09:00.

No LLM is consulted anywhere in this file. Which customer gets charged, chased,
or left alone is a decision that must be reconstructible from a table months
later, so it stays deterministic.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app import audit
from app import taxonomy as tx
from app.config import CONTACT_COST_INR
from app.models import RevenueEvent
from app.policy import compliance, stopping
from app.policy.playbooks import playbook_for

# A compliance block that ends the case, and the state it ends in.
TERMINAL_BLOCKS = {
    "do_not_contact": "suppressed",
    "hard_stop_root_cause": "escalated",
    "high_value_needs_human": "escalated",
    "frequency_cap_7d": "exhausted",
}


@dataclass
class Decision:
    kind: str                       # act | defer | skip | stop
    reason: str
    action: str | None = None
    channel: str | None = None
    timing: str | None = None
    step_index: int | None = None
    retry_at: datetime | None = None
    status: str | None = None       # terminal status when kind == "stop"
    rule: str | None = None
    origin: str = "playbook"        # playbook | stopping | compliance


def decide(db: Session, event: RevenueEvent, now: datetime) -> Decision:
    playbook = playbook_for(event.root_cause, event.arm)

    # What the next rung would cost, so the cost ceiling can refuse to commit
    # it rather than noticing after the money is spent.
    next_cost = 0.0
    if event.attempt_count < playbook.max_attempts:
        next_cost = CONTACT_COST_INR.get(playbook.steps[event.attempt_count].channel, 0.0)

    # --- gate 1: is this case still alive? --------------------------------
    stop = stopping.check(event, playbook, now, next_step_cost=next_cost)
    if stop.should_stop:
        d = Decision(kind="stop", reason=stop.reason, status=stop.status,
                     rule=stop.rule, origin="stopping")
        _log(db, event, now, d, playbook)
        return d

    # --- gate 2: what does the playbook say? ------------------------------
    step = playbook.steps[event.attempt_count]

    # --- gate 3: may we do it, now? ---------------------------------------
    verdict = compliance.evaluate(
        db, event, step.action, step.channel, now,
        timing=step.timing,
        enforce=(event.arm == "agent"),
    )

    if verdict.result == compliance.DEFER:
        d = Decision(
            kind="defer", reason=verdict.reason, rule=verdict.rule,
            action=step.action, channel=step.channel, timing=step.timing,
            step_index=event.attempt_count, origin="compliance",
            retry_at=verdict.retry_at or (now + timedelta(hours=1)),
        )
        _log(db, event, now, d, playbook)
        return d

    if verdict.result == compliance.BLOCK:
        terminal = TERMINAL_BLOCKS.get(verdict.rule or "")
        if terminal:
            d = Decision(kind="stop", reason=verdict.reason, rule=verdict.rule,
                         status=terminal, action=step.action, channel=step.channel,
                         step_index=event.attempt_count, origin="compliance")
        else:
            # Not fatal to the case — this rung of the ladder is unusable
            # (no consent on that channel, instrument can't be re-presented),
            # so burn the step and try the next one shortly.
            d = Decision(kind="skip", reason=verdict.reason, rule=verdict.rule,
                         action=step.action, channel=step.channel,
                         step_index=event.attempt_count, origin="compliance",
                         retry_at=now + timedelta(minutes=15))
        _log(db, event, now, d, playbook)
        return d

    # Allowed — but if a rule fired and was only advisory (baseline arm), the
    # violation is recorded. This is the whole basis of the compliance
    # comparison between the two policies: without it, the baseline's
    # quiet-hours messages and dead-instrument retries leave no evidence.
    if verdict.rule:
        audit.record(
            db, event_id=event.id, stage="violation", occurred_at=now,
            decision=(f"Policy rule '{verdict.rule}' would have blocked "
                      f"{step.action} via {step.channel}, but is not enforced "
                      f"under the {event.arm} policy. Proceeding. {verdict.reason}"),
            detail={"rule": verdict.rule, "action": step.action,
                    "channel": step.channel, "arm": event.arm, "enforced": False},
        )

    d = Decision(
        kind="act",
        reason=(f"{tx.root_cause(event.root_cause).label} → step "
                f"{event.attempt_count + 1}/{playbook.max_attempts}: "
                f"{step.action} via {step.channel}."
                + (f" {step.note}" if step.note else "")),
        action=step.action, channel=step.channel, timing=step.timing,
        step_index=event.attempt_count,
    )
    _log(db, event, now, d, playbook)
    return d


def _log(db, event, now, d: Decision, playbook):
    # Built per-branch, not as a dict: a dict literal would eagerly format
    # every variant, including the ones whose fields are None.
    if d.kind == "act":
        headline = f"Decided: {d.action} via {d.channel}."
    elif d.kind == "defer":
        when = f"{d.retry_at:%Y-%m-%d %H:%M UTC}" if d.retry_at else "later"
        headline = f"Deferred {d.action} to {when} [{d.rule}]."
    elif d.kind == "skip":
        headline = f"Skipped {d.action} via {d.channel} [{d.rule}]."
    else:
        headline = f"Stopped, case marked '{d.status}' [{d.rule}]."

    stage = {"playbook": "decide", "stopping": "stop", "compliance": "comply"}[d.origin]

    audit.record(
        db, event_id=event.id, stage=stage, occurred_at=now,
        decision=f"{headline} {d.reason}",
        detail={
            "kind": d.kind, "action": d.action, "channel": d.channel,
            "rule": d.rule, "origin": d.origin, "step_index": d.step_index,
            "attempt_count": event.attempt_count,
            "contact_count": event.contact_count,
            "playbook_steps": playbook.max_attempts,
            "playbook_rationale": playbook.rationale,
            "retry_at": d.retry_at,
        },
    )
