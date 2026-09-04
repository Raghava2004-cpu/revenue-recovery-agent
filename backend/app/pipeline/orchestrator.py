"""
The recovery scheduler.

The single biggest thing missing from a naive version of this project is *time*.
If detect→diagnose→decide→act runs once, end to end, in a single tick, then
"max 3 retries" is unreachable code, a retry sequencer is impossible, quiet
hours never bind, and intent decay can't be modelled. Every interesting
behaviour in a recovery agent lives in the gaps between attempts.

So this module runs the pipeline against a **virtual clock**. Cases are pushed
onto a priority queue keyed by when their next action is due; the clock jumps to
each due time in order and processes it. A 60-case batch therefore simulates two
weeks of recovery journeys — with real backoff, deferred quiet-hours messages,
salary-cycle retries and promise-to-pay follow-ups — in about a second.

Every case is run twice, once under the agent policy and once under a naive
baseline, sharing a customer and a random seed. See sim/outcome.py for why that
pairing is what makes the reported lift a measurement.
"""
import heapq
import itertools
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app import audit
from app import taxonomy as tx
from app.ai import client as ai_client
from app.config import MERCHANT_TZ, POLICY_VERSION
from app.models import Promise, RevenueEvent
from app.pipeline.act import act, settle_promise
from app.pipeline.decide import decide
from app.pipeline.detect import detect
from app.pipeline.diagnose import diagnose
from app.policy.playbooks import playbook_for

ARMS = ("agent", "baseline")

# Dates in the month when salaried customers in India are most likely to have
# just been credited. Used as a scheduling heuristic only — the agent has no
# access to any customer's actual balance.
SALARY_DAYS = (1, 2, 7, 15)
SALARY_HOUR_IST = 11
SALARY_SEARCH_CAP = timedelta(days=6)


def _salary_cycle_target(now: datetime, base_delay: timedelta) -> tuple[datetime, str]:
    """
    Move a retry onto the next plausible credit date instead of re-presenting
    into an account we already know was empty.

    This is a heuristic over the calendar, not a lookup of the customer's
    balance. It is the same reasoning a collections manager applies by hand,
    and it is the largest single source of the agent's lift on funds-related
    failures.
    """
    earliest = now + base_delay
    local = earliest.astimezone(MERCHANT_TZ)
    cap = earliest + SALARY_SEARCH_CAP

    probe = local.replace(hour=SALARY_HOUR_IST, minute=0, second=0, microsecond=0)
    if probe < local:
        probe += timedelta(days=1)

    while probe.astimezone(timezone.utc) <= cap:
        if probe.day in SALARY_DAYS:
            target = probe.astimezone(timezone.utc)
            return target, (f"deferred to {probe:%d %b %H:%M} IST — the next likely "
                            f"salary-credit date, rather than re-presenting into a "
                            f"balance that was already short")
        probe += timedelta(days=1)

    return earliest, ("no credit date falls inside the retry window; using the "
                      "standard backoff")


def _next_due(event: RevenueEvent, now: datetime) -> tuple[datetime, str]:
    """When should the step after the one just taken run?"""
    playbook = playbook_for(event.root_cause, event.arm)
    if event.attempt_count >= playbook.max_attempts:
        return now, "ladder exhausted"

    step = playbook.steps[event.attempt_count]
    if step.timing == "salary_cycle":
        return _salary_cycle_target(now, step.delay)
    return now + step.delay, f"standard backoff of {_humanise(step.delay)}"


def _humanise(delta: timedelta) -> str:
    secs = delta.total_seconds()
    if secs < 3600:
        return f"{secs / 60:.0f}m"
    if secs < 86400:
        return f"{secs / 3600:.0f}h"
    return f"{secs / 86400:.0f}d"


def run_batch(
    db: Session, cases: list[dict], horizon_days: int = 14,
    use_live_razorpay: bool = False, now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)

    # The clock starts at the OLDEST case, not at wall-time. A production agent
    # is running continuously, so a checkout abandoned four days ago was chased
    # four days ago — not now. Anchoring to wall-time instead would apply four
    # days of intent decay before the first message and score every abandonment
    # case at the floor, which is a simulation artefact, not agent behaviour.
    start = min((c.get("detected_at") or now) for c in cases) if cases else now
    horizon_end = start + timedelta(days=horizon_days)
    ai_client.usage.reset()

    audit.record(
        db, stage="detect", occurred_at=now,
        decision=(f"Batch started: {len(cases)} cases × {len(ARMS)} policy arms, "
                  f"{horizon_days}-day recovery horizon, policy {POLICY_VERSION}."),
        detail={"cases": len(cases), "arms": list(ARMS),
                "horizon_days": horizon_days,
                "llm_enabled": ai_client.available()},
    )

    queue: list[tuple[datetime, int, int]] = []
    tiebreak = itertools.count()

    # --- seed: detect + diagnose every case under both policies -----------
    for raw in cases:
        for arm in ARMS:
            event = detect(db, raw, arm=arm, now=now)
            diagnose(db, event, now=event.detected_at)

            playbook = playbook_for(event.root_cause, arm)
            first = playbook.steps[0]
            detected = _aware(event.detected_at)
            if first.timing == "salary_cycle":
                due, _ = _salary_cycle_target(detected, first.delay)
            else:
                due = detected + first.delay

            event.next_action_at = due
            db.commit()
            heapq.heappush(queue, (due, next(tiebreak), event.id))

    # --- run the clock ----------------------------------------------------
    processed = 0
    while queue:
        due, _, event_id = heapq.heappop(queue)
        if due > horizon_end:
            continue

        clock = due
        event = db.get(RevenueEvent, event_id)
        if event is None or event.status in ("recovered", "escalated",
                                             "suppressed", "exhausted"):
            continue

        processed += 1

        # A promise that has come due settles before anything else happens.
        promise = (db.query(Promise)
                   .filter(Promise.event_id == event.id, Promise.status == "open")
                   .order_by(Promise.promised_for).first())
        if promise and _aware(promise.promised_for) <= clock:
            if settle_promise(db, event, promise, clock):
                continue
            # Broken promise: straight back onto the ladder.

        decision = decide(db, event, clock)

        if decision.kind == "stop":
            event.status = decision.status or "exhausted"
            event.resolved_at = clock
            event.next_action_at = None
            db.commit()
            continue

        if decision.kind in ("defer", "skip"):
            if decision.kind == "skip":
                event.attempt_count += 1     # burn the unusable rung
            retry_at = decision.retry_at or (clock + timedelta(hours=1))
            event.next_action_at = retry_at
            db.commit()
            if retry_at <= horizon_end:
                heapq.heappush(queue, (retry_at, next(tiebreak), event.id))
            continue

        action_row = act(db, event, decision.action, decision.channel, clock,
                         use_live_razorpay=use_live_razorpay)

        if event.status in ("recovered", "escalated", "suppressed"):
            event.next_action_at = None
            db.commit()
            continue

        if action_row.outcome == "promised":
            open_promise = (db.query(Promise)
                            .filter(Promise.event_id == event.id, Promise.status == "open")
                            .order_by(Promise.promised_for.desc()).first())
            when = _aware(open_promise.promised_for) if open_promise else clock + timedelta(days=5)
            event.next_action_at = when
            db.commit()
            if when <= horizon_end:
                heapq.heappush(queue, (when, next(tiebreak), event.id))
            continue

        next_at, why = _next_due(event, clock)
        playbook = playbook_for(event.root_cause, event.arm)
        if event.attempt_count >= playbook.max_attempts:
            continue    # the stopping rule will close it on the next pass

        event.next_action_at = next_at
        db.commit()
        audit.record(
            db, event_id=event.id, stage="decide", occurred_at=clock,
            decision=f"Next attempt scheduled for {next_at:%Y-%m-%d %H:%M UTC} — {why}.",
            detail={"next_action_at": next_at, "rationale": why},
        )
        if next_at <= horizon_end:
            heapq.heappush(queue, (next_at, next(tiebreak), event.id))

    # --- close out anything still open at the horizon ---------------------
    stranded = (db.query(RevenueEvent)
                .filter(RevenueEvent.status.in_(["detected", "in_recovery"]))
                .all())
    for event in stranded:
        event.status = "exhausted"
        event.next_action_at = None
    if stranded:
        db.commit()
        audit.record(
            db, stage="stop", occurred_at=horizon_end,
            decision=(f"Horizon reached: {len(stranded)} cases were still mid-journey "
                      f"and are closed as unrecovered rather than left pending."),
            detail={"stranded": len(stranded), "horizon_days": horizon_days},
        )

    summary = {
        "cases": len(cases),
        "events": len(cases) * len(ARMS),
        "scheduled_actions_processed": processed,
        "horizon_days": horizon_days,
        "policy_version": POLICY_VERSION,
        "llm": ai_client.usage.snapshot(),
    }
    audit.record(db, stage="stop", occurred_at=horizon_end,
                 decision="Batch complete.", detail=summary)
    return summary


def run_live_event(db: Session, raw: dict, use_live_razorpay: bool = True) -> RevenueEvent:
    """
    The webhook path: one real event, agent policy, wall-clock time.

    Only the first action is taken inline so the webhook returns fast. The
    remaining ladder is picked up by /recovery/tick, which a scheduler (cron,
    Celery beat, a Cloud Scheduler job) calls on an interval in production.
    """
    now = datetime.now(timezone.utc)
    event = detect(db, raw, arm="agent", now=now)
    diagnose(db, event, now=now)

    decision = decide(db, event, now)
    if decision.kind == "act":
        act(db, event, decision.action, decision.channel, now,
            use_live_razorpay=use_live_razorpay)
        if event.status not in ("recovered", "escalated", "suppressed"):
            event.next_action_at, _ = _next_due(event, now)
    elif decision.kind == "stop":
        event.status = decision.status or "exhausted"
        event.resolved_at = now
    else:
        event.next_action_at = decision.retry_at
        if decision.kind == "skip":
            event.attempt_count += 1

    db.commit()
    db.refresh(event)
    return event


def tick(db: Session, use_live_razorpay: bool = True, limit: int = 100) -> dict:
    """Advance every live case whose next action is now due. Idempotent."""
    now = datetime.now(timezone.utc)
    due = (db.query(RevenueEvent)
           .filter(RevenueEvent.status.in_(["detected", "in_recovery"]),
                   RevenueEvent.next_action_at.isnot(None),
                   RevenueEvent.next_action_at <= now)
           .limit(limit).all())

    advanced = 0
    for event in due:
        decision = decide(db, event, now)
        if decision.kind == "stop":
            event.status = decision.status or "exhausted"
            event.resolved_at = now
            event.next_action_at = None
        elif decision.kind in ("defer", "skip"):
            if decision.kind == "skip":
                event.attempt_count += 1
            event.next_action_at = decision.retry_at
        else:
            act(db, event, decision.action, decision.channel, now,
                use_live_razorpay=use_live_razorpay)
            if event.status in ("recovered", "escalated", "suppressed"):
                event.next_action_at = None
            else:
                event.next_action_at, _ = _next_due(event, now)
        advanced += 1
        db.commit()

    return {"due": len(due), "advanced": advanced, "at": now.isoformat()}


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
