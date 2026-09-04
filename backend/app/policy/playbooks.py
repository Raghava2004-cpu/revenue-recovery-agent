"""
Recovery playbooks: root cause -> an ordered, time-spaced ladder of interventions.

Two design rules, both deliberate:

1. **A playbook is data, not code.** Every step the agent can take is declared
   here, so "why did it do that" is answerable by reading a table rather than
   tracing execution. Nothing downstream may invent a step.

2. **Escalation is by channel and by cost, in that order.** Cheap and silent
   first (a gateway retry costs ₹0 and annoys nobody), then messaging, then a
   voice call, then a human. A ladder that opens with the expensive channel
   burns margin on cases that would have self-resolved.

The `timing` field is where most of the recovered money actually comes from.
`salary_cycle` defers a retry until the customer's account is plausibly funded
instead of re-presenting into the same empty balance — see sim/outcome.py for
why an immediate retry on INSUFFICIENT_FUNDS is close to a coin with no heads.
"""
from dataclasses import dataclass
from datetime import timedelta

from app import taxonomy as tx


@dataclass(frozen=True)
class Step:
    action: str
    channel: str
    delay: timedelta          # measured from the previous attempt (or detection)
    timing: str | None = None  # None | "salary_cycle" | "business_hours"
    note: str = ""


@dataclass(frozen=True)
class Playbook:
    steps: tuple[Step, ...]
    max_contacts: int          # ladder length is not the same as contact budget
    rationale: str

    @property
    def max_attempts(self) -> int:
        return len(self.steps)


_M = timedelta(minutes=1)
_H = timedelta(hours=1)
_D = timedelta(days=1)


PLAYBOOKS: dict[str, Playbook] = {

    "INSUFFICIENT_FUNDS": Playbook(
        steps=(
            Step(tx.RETRY_SAME_INSTRUMENT, tx.SYSTEM, 6 * _H, "salary_cycle",
                 "Silent re-presentment, deferred to the next likely credit. Costs "
                 "nothing and needs no customer action."),
            Step(tx.SEND_REMINDER, tx.WHATSAPP, 18 * _H, None,
                 "Only now do we spend a message — after the free option failed."),
            Step(tx.RETRY_SAME_INSTRUMENT, tx.SYSTEM, 3 * _D, "salary_cycle",
                 "Second credit cycle. Still free, still silent."),
            Step(tx.ESCALATE_HUMAN, tx.INTERNAL_QUEUE, 2 * _D, "business_hours",
                 "Two funded windows missed — this is a collections conversation."),
        ),
        max_contacts=2,
        rationale="The instrument is fine and the customer is not refusing. Timing "
                  "is the whole game, so spend attempts on *when*, not on volume.",
    ),

    "CARD_EXPIRED": Playbook(
        steps=(
            Step(tx.REGENERATE_PAYMENT_LINK, tx.WHATSAPP, 15 * _M, None,
                 "Straight to a new instrument. Retrying the stored card is a "
                 "guaranteed decline, so it is not in this ladder at all."),
            Step(tx.REGENERATE_PAYMENT_LINK, tx.SMS, 1 * _D, None,
                 "Different rail in case WhatsApp isn't where they read."),
            Step(tx.VOICE_CALL_HINGLISH, tx.VOICE, 3 * _D, "business_hours",
                 "Voice earns its ₹2.50 only after two silent messages."),
            Step(tx.ESCALATE_HUMAN, tx.INTERNAL_QUEUE, 3 * _D, "business_hours", ""),
        ),
        max_contacts=3,
        rationale="Deterministically unrecoverable on the existing card. The only "
                  "question is which channel gets the customer to a new one.",
    ),

    "CARD_DECLINED_BY_BANK": Playbook(
        steps=(
            Step(tx.OFFER_ALTERNATE_METHOD, tx.WHATSAPP, 30 * _M, None,
                 "Switch rails to UPI rather than re-presenting into the same "
                 "issuer decline and tripping velocity limits."),
            Step(tx.REGENERATE_PAYMENT_LINK, tx.SMS, 1 * _D, None, ""),
            Step(tx.ESCALATE_HUMAN, tx.INTERNAL_QUEUE, 4 * _D, "business_hours", ""),
        ),
        max_contacts=2,
        rationale="Issuer said no without saying why. Changing the rail is far more "
                  "productive — and safer — than asking the same issuer again.",
    ),

    "AUTH_FAILED_OTP": Playbook(
        steps=(
            Step(tx.REGENERATE_PAYMENT_LINK, tx.WHATSAPP, 5 * _M, None,
                 "Highest-intent case in the whole taxonomy: they were mid-payment. "
                 "Five minutes, while they still have the phone in hand."),
            Step(tx.REGENERATE_PAYMENT_LINK, tx.SMS, 3 * _H, None, ""),
            Step(tx.ESCALATE_HUMAN, tx.INTERNAL_QUEUE, 3 * _D, "business_hours", ""),
        ),
        max_contacts=2,
        rationale="Willing customer, broken auth step. Speed dominates; every hour "
                  "of delay costs more conversion than any copy change can win back.",
    ),

    "GATEWAY_TIMEOUT": Playbook(
        steps=(
            Step(tx.RETRY_SAME_INSTRUMENT, tx.SYSTEM, 2 * _M, None,
                 "Transient fault. Retry immediately, silently, for free."),
            Step(tx.RETRY_SAME_INSTRUMENT, tx.SYSTEM, 30 * _M, None, ""),
            Step(tx.REGENERATE_PAYMENT_LINK, tx.WHATSAPP, 6 * _H, None,
                 "Only bother the customer if the rail is still broken hours later."),
            Step(tx.ESCALATE_HUMAN, tx.INTERNAL_QUEUE, 2 * _D, "business_hours", ""),
        ),
        max_contacts=1,
        rationale="Nothing is wrong with the customer or the instrument, so the "
                  "correct intervention is the one they never find out about.",
    ),

    "UPI_COLLECT_EXPIRED": Playbook(
        steps=(
            Step(tx.REGENERATE_PAYMENT_LINK, tx.WHATSAPP, 10 * _M, None,
                 "Fresh intent link while they're still on their phone."),
            Step(tx.SEND_REMINDER, tx.SMS, 4 * _H, None, ""),
            Step(tx.ESCALATE_HUMAN, tx.INTERNAL_QUEUE, 3 * _D, "business_hours", ""),
        ),
        max_contacts=2,
        rationale="The request expired unapproved — re-ask quickly, don't re-ask often.",
    ),

    "RISK_BLOCKED": Playbook(
        steps=(
            Step(tx.SUPPRESS, tx.SYSTEM, timedelta(0), None,
                 "Stop immediately. Re-presenting a risk-declined payment is the "
                 "behaviour card-scheme rules exist to prevent."),
            Step(tx.ESCALATE_HUMAN, tx.INTERNAL_QUEUE, timedelta(0), None,
                 "A human reviews it; the agent does not touch it again."),
        ),
        max_contacts=0,
        rationale="The compliant recovery rate on a risk block is zero. Encoding "
                  "that is the feature.",
    ),

    "MANDATE_REVOKED": Playbook(
        steps=(
            Step(tx.REGENERATE_PAYMENT_LINK, tx.WHATSAPP, 2 * _H, None,
                 "Re-consent link. Debiting under a revoked mandate would be an "
                 "unauthorised debit, so no mandate retry appears in this ladder."),
            Step(tx.SEND_REMINDER, tx.SMS, 2 * _D, None, ""),
            Step(tx.ESCALATE_HUMAN, tx.INTERNAL_QUEUE, 4 * _D, "business_hours", ""),
        ),
        max_contacts=2,
        rationale="Authorisation was withdrawn. Recovery means asking for it back, "
                  "never routing around it.",
    ),

    "MANDATE_LIMIT_EXCEEDED": Playbook(
        steps=(
            Step(tx.REGENERATE_PAYMENT_LINK, tx.WHATSAPP, 1 * _H, None,
                 "One-off link for this cycle's amount; the mandate cap makes an "
                 "auto-debit retry mathematically impossible."),
            Step(tx.ESCALATE_HUMAN, tx.INTERNAL_QUEUE, 3 * _D, "business_hours",
                 "Amending the mandate cap needs a human."),
        ),
        max_contacts=1,
        rationale="Valid mandate, wrong ceiling. Collect this cycle manually and "
                  "fix the ceiling out-of-band.",
    ),

    "SUBSCRIPTION_INSUFFICIENT_FUNDS": Playbook(
        steps=(
            Step(tx.RETRY_MANDATE, tx.SYSTEM, 12 * _H, "salary_cycle",
                 "Auto-debit re-presentment aligned to the salary cycle."),
            Step(tx.SEND_REMINDER, tx.WHATSAPP, 1 * _D, None,
                 "Pre-warn before the next debit so the balance is there."),
            Step(tx.RETRY_MANDATE, tx.SYSTEM, 2 * _D, "salary_cycle", ""),
            Step(tx.ESCALATE_HUMAN, tx.INTERNAL_QUEUE, 3 * _D, "business_hours",
                 "Involuntary churn risk — worth a human before the subscription dies."),
        ),
        max_contacts=2,
        rationale="Live mandate, empty account on the debit date. This is the "
                  "canonical case for a salary-cycle-aware retry sequencer.",
    ),

    "CHECKOUT_ABANDONED_PAYMENT_PAGE": Playbook(
        steps=(
            Step(tx.REGENERATE_PAYMENT_LINK, tx.WHATSAPP, 20 * _M, None,
                 "Purchase intent halves within hours. Twenty minutes, not a day."),
            Step(tx.SEND_REMINDER, tx.SMS, 1 * _D, None,
                 "One follow-up. Abandonment is not a debt; two nudges is the limit "
                 "before this becomes spam."),
        ),
        max_contacts=2,
        rationale="Low ticket, decaying intent, no obligation to pay. Cheap, fast, "
                  "and short — then let it go.",
    ),

    "CHECKOUT_ABANDONED_METHOD_MISSING": Playbook(
        steps=(
            Step(tx.OFFER_ALTERNATE_METHOD, tx.WHATSAPP, 20 * _M, None,
                 "They left while looking for a rail we didn't show. Offer it."),
            Step(tx.SEND_REMINDER, tx.SMS, 1 * _D, None, ""),
        ),
        max_contacts=2,
        rationale="Re-sending the same checkout that already failed them is the "
                  "single most common wasted dunning message.",
    ),

    "INVOICE_OVERDUE_CASHFLOW": Playbook(
        steps=(
            Step(tx.SEND_REMINDER, tx.EMAIL, 2 * _H, "business_hours",
                 "Email first: it reaches AP, it's on record, it costs ₹0.02."),
            Step(tx.REQUEST_PROMISE_TO_PAY, tx.WHATSAPP, 3 * _D, "business_hours",
                 "Negotiate a date instead of escalating pressure. A kept promise "
                 "recovers more than a harder chase."),
            Step(tx.VOICE_CALL_HINGLISH, tx.VOICE, 5 * _D, "business_hours",
                 "Voice only once a promise exists to reference or has been broken."),
            Step(tx.ESCALATE_HUMAN, tx.INTERNAL_QUEUE, 5 * _D, "business_hours", ""),
        ),
        max_contacts=3,
        rationale="B2B buyers who intend to pay respond to a negotiated date. The "
                  "expensive channels are gated behind a broken promise.",
    ),

    "INVOICE_OVERDUE_PROCESS": Playbook(
        steps=(
            Step(tx.SEND_REMINDER, tx.EMAIL, 1 * _H, "business_hours",
                 "Usually a missing PO number, not a refusal to pay."),
            Step(tx.VOICE_CALL_HINGLISH, tx.VOICE, 2 * _D, "business_hours",
                 "Reaching the right human in AP resolves these in one call."),
            Step(tx.ESCALATE_HUMAN, tx.INTERNAL_QUEUE, 4 * _D, "business_hours", ""),
        ),
        max_contacts=2,
        rationale="Stuck in the buyer's process. Chasing harder does nothing; "
                  "reaching the right desk does everything.",
    ),

    "INVOICE_DISPUTED": Playbook(
        steps=(
            Step(tx.SUPPRESS, tx.SYSTEM, timedelta(0), None,
                 "Automated dunning against a live dispute is a legal and "
                 "relationship risk. Stop."),
            Step(tx.ESCALATE_HUMAN, tx.INTERNAL_QUEUE, timedelta(0), None, ""),
        ),
        max_contacts=0,
        rationale="Disputes are resolved by people, not by reminders.",
    ),

    "UNKNOWN": Playbook(
        steps=(
            Step(tx.ESCALATE_HUMAN, tx.INTERNAL_QUEUE, timedelta(0), None,
                 "No confident diagnosis. We do not guess with money."),
        ),
        max_contacts=0,
        rationale="An unclassified failure is a human's problem, not an excuse to "
                  "try something and see.",
    ),
}


# The control arm: what an untuned dunning cron actually does. Same instrument,
# fixed intervals, same channel, regardless of why the payment failed. This is
# the thing the agent has to beat, and it is deliberately not a strawman —
# it is a real retry ladder that recovers real money on transient failures.
BASELINE_PLAYBOOK = Playbook(
    steps=(
        Step(tx.RETRY_SAME_INSTRUMENT, tx.SYSTEM, 5 * _M, None, "Retry now."),
        Step(tx.SEND_REMINDER, tx.SMS, 1 * _H, None, "Blast an SMS."),
        Step(tx.RETRY_SAME_INSTRUMENT, tx.SYSTEM, 6 * _H, None, "Retry again."),
        Step(tx.SEND_REMINDER, tx.SMS, 1 * _D, None, "Blast another SMS."),
    ),
    max_contacts=2,
    rationale="Fixed 3-strike retry ladder, cause-blind and clock-blind.",
)


def playbook_for(root_cause_code: str | None, arm: str = "agent") -> Playbook:
    if arm == "baseline":
        return BASELINE_PLAYBOOK
    return PLAYBOOKS.get(root_cause_code or "", PLAYBOOKS["UNKNOWN"])
