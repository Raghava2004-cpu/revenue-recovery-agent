"""
ACT — execute the decided intervention and observe what happened.

Where the outcome comes from:

  live mode   a real Razorpay test-mode Payment Link is created. The result is
              genuinely pending until a `payment_link.paid` webhook arrives —
              we do not pretend to know.
  batch mode  the outcome model in sim/outcome.py scores the attempt. Every
              factor that produced the number is written to the audit trail, so
              a reviewer can see exactly why an attempt was judged to succeed.

Escalations are recorded as handed-off, never as recovered, even though a real
collections desk closes a share of them. Counting them would inflate the
headline number with money the agent didn't bring in.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app import audit, razorpay_client
from app import taxonomy as tx
from app.ai import message_llm
from app.config import CONTACT_COST_INR
from app.models import Promise, RecoveryAction, RevenueEvent
from app.sim import outcome

log = logging.getLogger(__name__)

PROMISE_HORIZON = timedelta(days=5)


def act(
    db: Session, event: RevenueEvent, action: str, channel: str,
    now: datetime, use_live_razorpay: bool = False,
) -> RecoveryAction:
    attempt_no = event.attempt_count + 1
    cost = CONTACT_COST_INR.get(channel, 0.0)

    # --- terminal actions -------------------------------------------------
    if action in (tx.ESCALATE_HUMAN, tx.SUPPRESS):
        return _terminal(db, event, action, channel, now, attempt_no, cost)

    # --- copy -------------------------------------------------------------
    message, message_source = ("", None)
    if action in tx.CONTACT_ACTIONS:
        message, message_source = message_llm.build_message(
            action=action, channel=channel, root_cause=event.root_cause or "UNKNOWN",
            language=event.language or "hinglish", customer_name=event.customer_name,
            amount=event.amount, attempt_no=event.contact_count + 1,
            event_type=event.event_type,
        )

    # --- a real Razorpay Payment Link, when running live ------------------
    link_url = None
    live_error = None
    if use_live_razorpay and action in (tx.REGENERATE_PAYMENT_LINK, tx.OFFER_ALTERNATE_METHOD):
        try:
            link = razorpay_client.create_recovery_payment_link(
                amount_paise=int(round(event.amount * 100)),
                description=f"Recovery for {event.event_type} ({event.root_cause})",
                customer={
                    "name": event.customer_name or "Customer",
                    "contact": event.customer_contact or "",
                    "email": event.customer_email or "",
                },
                notes={"case_key": event.case_key, "root_cause": event.root_cause or "",
                       "attempt": str(attempt_no)},
            )
            link_url = link.get("short_url")
        except Exception as exc:                            # noqa: BLE001
            live_error = str(exc)
            log.warning("Razorpay link creation failed for event %s: %s", event.id, exc)

    # Substitute the link token. In live mode that's the real short URL Razorpay
    # returned; in batch mode it's a clearly-marked placeholder in the same shape,
    # so the audit trail shows the message exactly as the customer would receive
    # it rather than leaking an unrendered template token.
    if message and message_llm.LINK_TOKEN in message:
        message = message.replace(
            message_llm.LINK_TOKEN,
            link_url or f"rzp.io/i/{_demo_link_id(event, attempt_no)}",
        )

    # --- outcome ----------------------------------------------------------
    factors: dict = {}
    probability = None

    if live_error:
        result, amount_recovered = "failed", 0.0
    elif link_url:
        # Real link out to a real customer: the answer arrives by webhook.
        result, amount_recovered = "pending", 0.0
    else:
        succeeded, probability, factors = outcome.attempt_succeeds(
            event, action, channel, now, attempt_index=attempt_no
        )
        if action == tx.REQUEST_PROMISE_TO_PAY:
            result = "promised" if succeeded else "no_response"
            amount_recovered = 0.0
        else:
            result = "recovered" if succeeded else "no_response"
            amount_recovered = event.amount if succeeded else 0.0

    action_row = RecoveryAction(
        event_id=event.id, attempt_no=attempt_no, action_type=action, channel=channel,
        message=message or None, message_source=message_source,
        payment_link_url=link_url, outcome=result,
        amount_recovered=amount_recovered, cost=cost,
        success_probability=probability, scheduled_at=now, executed_at=now,
    )
    db.add(action_row)

    # --- state ------------------------------------------------------------
    event.attempt_count = attempt_no
    event.cost_incurred = (event.cost_incurred or 0.0) + cost
    if action in tx.CONTACT_ACTIONS:
        event.contact_count = (event.contact_count or 0) + 1

    if result == "recovered":
        event.status = "recovered"
        event.resolved_at = now
        event.amount_recovered = event.amount
    elif result == "promised":
        promise = Promise(event_id=event.id, amount=event.amount,
                          promised_for=now + PROMISE_HORIZON, status="open")
        db.add(promise)

    db.commit()
    db.refresh(action_row)

    audit.record(
        db, event_id=event.id, stage="act", occurred_at=now,
        decision=_narrate(action, channel, result, link_url, live_error, probability),
        detail={
            "attempt_no": attempt_no, "action": action, "channel": channel,
            "outcome": result, "cost_inr": cost,
            "amount_recovered": amount_recovered,
            "message_source": message_source, "message": message or None,
            "payment_link": link_url, "live_error": live_error,
            "outcome_model": factors or None,
        },
    )
    return action_row


def _demo_link_id(event: RevenueEvent, attempt_no: int) -> str:
    """A stable, obviously-fake short-link id for batch mode."""
    import hashlib
    seed = f"{event.case_key}|{event.arm}|{attempt_no}".encode()
    return hashlib.sha256(seed).hexdigest()[:10]


def _terminal(db, event, action, channel, now, attempt_no, cost) -> RecoveryAction:
    status = "escalated" if action == tx.ESCALATE_HUMAN else "suppressed"
    cause = tx.root_cause(event.root_cause)

    note = (f"Handed to the human recovery queue with the full diagnosis and "
            f"attempt history attached."
            if action == tx.ESCALATE_HUMAN else
            f"Suppressed — no autonomous action is permitted for {cause.label}.")

    action_row = RecoveryAction(
        event_id=event.id, attempt_no=attempt_no, action_type=action, channel=channel,
        message=None, outcome=status, amount_recovered=0.0, cost=cost,
        scheduled_at=now, executed_at=now,
    )
    db.add(action_row)

    event.attempt_count = attempt_no
    event.cost_incurred = (event.cost_incurred or 0.0) + cost
    event.status = status
    event.resolved_at = now
    db.commit()
    db.refresh(action_row)

    audit.record(
        db, event_id=event.id, stage="stop", occurred_at=now,
        decision=f"{action} → case closed as '{status}'. {note} {cause.note}",
        detail={"action": action, "status": status, "cost_inr": cost,
                "root_cause": event.root_cause,
                "amount_handed_off": event.amount if status == "escalated" else 0.0},
    )
    return action_row


def settle_promise(db: Session, event: RevenueEvent, promise: Promise,
                   now: datetime) -> bool:
    """
    A promise-to-pay comes due. Kept promises are the recovery; broken ones put
    the case back on the ladder with the broken promise on its record.
    """
    kept, probability, factors = outcome.promise_kept(
        event, attempt_index=1000 + event.attempt_count,   # distinct draw from attempts
    )
    promise.status = "kept" if kept else "broken"
    promise.settled_at = now

    if kept:
        event.status = "recovered"
        event.resolved_at = now
        event.amount_recovered = event.amount
    db.commit()

    audit.record(
        db, event_id=event.id, stage="observe", occurred_at=now,
        decision=(f"Promise to pay ₹{promise.amount:,.0f} due {promise.promised_for:%d %b} "
                  f"was {'kept — payment received' if kept else 'broken; case returns to the ladder'}."),
        detail={"promise_id": promise.id, "status": promise.status,
                "probability": probability, "outcome_model": factors},
    )
    return kept


def _narrate(action, channel, result, link_url, live_error, probability) -> str:
    if live_error:
        return f"Attempted {action} via {channel}; Razorpay API error: {live_error}"
    if link_url:
        return (f"Executed {action} via {channel}. Live Razorpay Payment Link "
                f"created ({link_url}); awaiting payment_link.paid webhook.")
    p = f" (modelled success probability {probability:.1%})" if probability is not None else ""
    verdict = {
        "recovered": "payment recovered",
        "no_response": "no response",
        "promised": "customer committed to a payment date",
        "failed": "attempt failed",
        "pending": "pending",
    }.get(result, result)
    return f"Executed {action} via {channel} → {verdict}{p}."
