"""
Stopping rules — the answer to "when does this agent give up?"

An agent that can start a recovery journey but never end one is a spam
generator with a database. Every rule here terminates a case, and every
termination is a distinct, reportable status rather than a silent drop:

  recovered   the money came back
  escalated   handed to a human, with the reason
  suppressed  policy forbade acting at all
  exhausted   the ladder ran out without success

The cost ceiling is the one people forget. Chasing a ₹199 subscription with
three WhatsApp messages and a voice call spends more than it can recover; the
agent is measured on *net* recovery, so it has to be able to walk away.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from app import taxonomy as tx
from app.models import RevenueEvent
from app.policy.playbooks import Playbook

# Never spend more than this share of the at-risk amount trying to recover it.
COST_CEILING_RATIO = 0.12

# A case older than this is closed regardless of ladder position — an abandoned
# checkout from three weeks ago is not a recovery opportunity, it's a nuisance.
MAX_CASE_AGE = {
    tx.CHECKOUT_ABANDONED: timedelta(days=3),
    tx.PAYMENT_FAILED: timedelta(days=10),
    tx.SUBSCRIPTION_FAILED: timedelta(days=14),
    tx.INVOICE_OVERDUE: timedelta(days=30),
}


@dataclass
class Stop:
    should_stop: bool
    status: str | None = None
    rule: str | None = None
    reason: str = ""


CONTINUE = Stop(False)


def check(event: RevenueEvent, playbook: Playbook, now: datetime,
          next_step_cost: float = 0.0) -> Stop:
    if event.status == "recovered":
        return Stop(True, "recovered", "already_recovered",
                    "Case already resolved; no further action.")

    if event.status in ("escalated", "suppressed", "exhausted"):
        return Stop(True, event.status, "already_terminal",
                    f"Case is already in terminal state '{event.status}'.")

    if event.attempt_count >= playbook.max_attempts:
        return Stop(True, "exhausted", "ladder_exhausted",
                    f"All {playbook.max_attempts} playbook steps attempted without "
                    f"recovery. Closing rather than looping.")

    if event.contact_count >= playbook.max_contacts and playbook.max_contacts > 0:
        # The ladder may have steps left, but the contact budget is spent —
        # remaining steps are only reachable if they're silent or internal.
        remaining = playbook.steps[event.attempt_count:]
        if all(s.action in tx.CONTACT_ACTIONS for s in remaining):
            return Stop(True, "exhausted", "contact_budget_spent",
                        f"Contact budget of {playbook.max_contacts} is spent and "
                        f"every remaining step would need another contact.")

    # The ceiling is checked against what the *next* step would cost, not only
    # what has already been spent. Checking spend alone lets the agent commit
    # a ₹45 human review on a ₹300 abandoned cart — the money is gone by the
    # time the rule notices.
    ceiling = event.amount * COST_CEILING_RATIO
    spent = event.cost_incurred or 0.0
    if spent + next_step_cost > ceiling:
        already = f"Spent ₹{spent:.2f}" if spent else "Nothing spent yet"
        return Stop(True, "exhausted", "cost_ceiling",
                    f"{already} chasing ₹{event.amount:,.0f}, and the next step "
                    f"costs ₹{next_step_cost:.2f} — over the "
                    f"{COST_CEILING_RATIO:.0%} ceiling of ₹{ceiling:.2f}. "
                    f"Continuing would destroy more value than it recovers.")

    max_age = MAX_CASE_AGE.get(event.event_type, timedelta(days=14))
    detected = event.detected_at
    if detected and detected.tzinfo is None:
        from datetime import timezone
        detected = detected.replace(tzinfo=timezone.utc)
    if detected and (now - detected) > max_age:
        return Stop(True, "exhausted", "case_too_old",
                    f"Case is {(now - detected).days} days old; the recovery window "
                    f"for {event.event_type} is {max_age.days} days.")

    return CONTINUE
