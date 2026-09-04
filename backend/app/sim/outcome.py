"""
The outcome model — how an attempt is judged to have worked or not.

READ THIS BEFORE TRUSTING ANY NUMBER ON THE DASHBOARD.

In batch mode there is no real customer to pay us, so an attempt's result comes
from this model. That makes the model the single most important piece of
intellectual honesty in the project, and it is built on three commitments:

1. **The action has to matter.** Probability is a function of (root cause,
   action, channel, timing, fatigue, customer). Retrying an expired card is
   scored 0.0 — not "low" — because a re-presentment against an expired card is
   declined deterministically, not unluckily. A model where every action has the
   same success rate would make the agent's judgment unmeasurable, which is the
   flaw this rewrite exists to fix.

2. **Common random numbers.** The luck of a case is drawn from
   sha256(case_key, attempt_index), so the agent arm and the baseline arm facing
   the same customer draw *identical* randomness. Any difference in recovered
   revenue between the arms is attributable to policy, not to variance. This is
   the standard paired-comparison trick from discrete-event simulation, and it
   is why a 60-case batch produces a stable, reproducible lift figure instead of
   a number that changes every run.

3. **The agent cannot see any of it.** `sim_propensity`, `sim_funds_at` and
   `sim_reachable` live on the event row but are read only here. No pipeline
   stage may branch on them — tests/test_no_oracle_leak.py greps the pipeline
   and policy packages to enforce that, because an agent that can peek at the
   ground truth would post spectacular and meaningless numbers.

Base rates below are plausible order-of-magnitude assumptions for the Indian
payments market, not measured constants. They are calibration inputs, and the
honest claim this project makes is about the *lift between two policies scored
by the same model*, which is far more robust to their exact values than an
absolute recovery rate would be.
"""
import hashlib
from datetime import datetime, timedelta, timezone

from app import taxonomy as tx
from app.models import RevenueEvent

# Probability that an ideal action, at an ideal time, on the first attempt,
# recovers the money. Everything else in this file scales this down.
BASE_RECOVERABILITY = {
    "INSUFFICIENT_FUNDS": 0.62,
    "CARD_EXPIRED": 0.55,
    "CARD_DECLINED_BY_BANK": 0.42,
    "AUTH_FAILED_OTP": 0.72,
    "GATEWAY_TIMEOUT": 0.88,
    "UPI_COLLECT_EXPIRED": 0.66,
    "RISK_BLOCKED": 0.0,
    "MANDATE_REVOKED": 0.30,
    "MANDATE_LIMIT_EXCEEDED": 0.48,
    "SUBSCRIPTION_INSUFFICIENT_FUNDS": 0.68,
    "CHECKOUT_ABANDONED_PAYMENT_PAGE": 0.28,
    "CHECKOUT_ABANDONED_METHOD_MISSING": 0.34,
    "INVOICE_OVERDUE_CASHFLOW": 0.55,
    "INVOICE_OVERDUE_PROCESS": 0.70,
    "INVOICE_DISPUTED": 0.0,
    "UNKNOWN": 0.05,
}

# Causes whose recovery is gated on money arriving in the account, not on
# persuasion. Retrying before that moment is near-futile however good the copy.
FUNDS_GATED = {"INSUFFICIENT_FUNDS", "SUBSCRIPTION_INSUFFICIENT_FUNDS"}

# Causes where purchase intent decays: the clock is the dominant variable.
INTENT_HALF_LIFE = {
    "CHECKOUT_ABANDONED_PAYMENT_PAGE": timedelta(hours=6),
    "CHECKOUT_ABANDONED_METHOD_MISSING": timedelta(hours=8),
    "AUTH_FAILED_OTP": timedelta(hours=3),
    "UPI_COLLECT_EXPIRED": timedelta(hours=4),
}

CHANNEL_EFFECTIVENESS = {
    tx.WHATSAPP: 1.00,   # read rates dominate SMS in India
    tx.SMS: 0.78,
    tx.VOICE: 1.20,      # most effective per contact, and 12x the cost
    tx.EMAIL: 0.55,      # weak B2C, adjusted up for B2B below
    tx.SYSTEM: 1.00,     # silent retry — no customer involvement to convert
    tx.INTERNAL_QUEUE: 1.00,
}

SEGMENT_FACTOR = {"loyal": 1.18, "returning": 1.00, "new": 0.82, "b2b": 1.00}


def draw(case_key: str, attempt_index: int) -> float:
    """
    Deterministic uniform [0,1) keyed on the case and attempt number.

    Both policy arms call this with the same case_key, so on their first
    attempt they face literally the same coin. This is what makes the A/B
    comparison a paired experiment rather than two independent samples.
    """
    h = hashlib.sha256(f"{case_key}|{attempt_index}".encode()).digest()
    return int.from_bytes(h[:8], "big") / float(1 << 64)


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def action_fit(cause: tx.RootCause, action: str) -> float:
    """
    How well does this action address this specific failure?

    This is the function that makes diagnosis worth doing. If it returned 1.0
    everywhere, the Diagnose stage would be decorative.
    """
    code = cause.code

    if action in (tx.RETRY_SAME_INSTRUMENT, tx.RETRY_MANDATE):
        if cause.needs_new_instrument:
            return 0.0      # expired card / revoked mandate: declined every time
        if not cause.auto_retry_safe:
            return 0.05     # issuer already said no; asking again rarely helps
        return 1.0

    if action == tx.REGENERATE_PAYMENT_LINK:
        if cause.needs_new_instrument:
            return 1.00     # the whole point: get them onto a working instrument
        if code in ("AUTH_FAILED_OTP", "UPI_COLLECT_EXPIRED"):
            return 1.00
        if code.startswith("CHECKOUT_ABANDONED"):
            return 0.95
        if code in FUNDS_GATED:
            return 0.45     # a link does not put money in the account
        return 0.75

    if action == tx.OFFER_ALTERNATE_METHOD:
        if code == "CARD_DECLINED_BY_BANK":
            return 1.10     # switching rails routes around the issuer entirely
        if code == "CHECKOUT_ABANDONED_METHOD_MISSING":
            return 1.20     # gives them exactly what they left looking for
        return 0.80

    if action == tx.SEND_REMINDER:
        if code in FUNDS_GATED:
            return 0.80     # a heads-up before the next debit genuinely works
        if cause.family == "receivable":
            return 0.90
        if cause.needs_new_instrument:
            return 0.30     # nagging without a new-instrument link is weak
        return 0.62

    if action == tx.VOICE_CALL_HINGLISH:
        return 1.25 if cause.family == "receivable" else 1.00

    if action == tx.REQUEST_PROMISE_TO_PAY:
        return 1.30 if code == "INVOICE_OVERDUE_CASHFLOW" else 0.40

    # Escalation and suppression never recover money autonomously. Escalated
    # cases are reported as handed-off, never as recovered.
    return 0.0


def timing_fit(event: RevenueEvent, cause: tx.RootCause, now: datetime) -> tuple[float, str]:
    """Returns (multiplier, human-readable explanation) for the drill-down UI."""
    if cause.code in FUNDS_GATED:
        funds_at = _aware(event.sim_funds_at)
        if funds_at and now < funds_at:
            hrs = (funds_at - now).total_seconds() / 3600
            return 0.06, (f"account is still short — next likely credit is "
                          f"{hrs:.0f}h away")
        return 1.0, "attempted after the account was replenished"

    half_life = INTENT_HALF_LIFE.get(cause.code)
    if half_life:
        detected = _aware(event.detected_at) or now
        elapsed = max((now - detected).total_seconds(), 0.0)
        decay = 0.5 ** (elapsed / half_life.total_seconds())
        decay = max(decay, 0.15)
        return decay, (f"{elapsed / 3600:.1f}h after drop-off; intent half-life is "
                       f"{half_life.total_seconds() / 3600:.0f}h")

    return 1.0, "timing is not a material factor for this cause"


def success_probability(
    event: RevenueEvent, action: str, channel: str, now: datetime,
) -> tuple[float, dict]:
    """Returns (probability, factor breakdown) — the breakdown is shown in the UI."""
    cause = tx.root_cause(event.root_cause)

    base = BASE_RECOVERABILITY.get(cause.code, 0.05)
    fit = action_fit(cause, action)
    timing, timing_note = timing_fit(event, cause, now)

    ch = CHANNEL_EFFECTIVENESS.get(channel, 1.0)
    if channel == tx.EMAIL and event.segment == "b2b":
        ch = 1.0     # email is the primary AP channel for receivables

    # Each additional contact converts worse than the last.
    fatigue = 0.86 ** max(event.contact_count, 0)

    # Bigger tickets need more deliberation from the payer.
    amount_factor = 1.0 if event.amount <= 2000 else max(0.72, 1.0 - 0.06 *
                                                         ((event.amount / 2000) ** 0.5))

    segment = SEGMENT_FACTOR.get(event.segment or "returning", 1.0)

    # Latent willingness/ability, fixed per customer and shared across arms.
    propensity = 0.35 + 0.9 * (event.sim_propensity if event.sim_propensity is not None else 0.5)

    reachable = 1.0
    if action in tx.CONTACT_ACTIONS and event.sim_reachable is False:
        reachable = 0.03    # dead phone number / bounced address

    p = base * fit * timing * ch * fatigue * amount_factor * segment * propensity * reachable
    p = max(0.0, min(p, 0.95))

    return p, {
        "base_recoverability": round(base, 3),
        "action_fit": round(fit, 3),
        "timing_fit": round(timing, 3),
        "timing_note": timing_note,
        "channel_effectiveness": round(ch, 3),
        "contact_fatigue": round(fatigue, 3),
        "amount_friction": round(amount_factor, 3),
        "segment": round(segment, 3),
        "customer_propensity": round(propensity, 3),
        "reachable": round(reachable, 3),
        "probability": round(p, 4),
    }


def attempt_succeeds(
    event: RevenueEvent, action: str, channel: str, now: datetime, attempt_index: int,
) -> tuple[bool, float, dict]:
    p, factors = success_probability(event, action, channel, now)
    u = draw(event.case_key, attempt_index)
    factors["random_draw"] = round(u, 4)
    return (u < p), p, factors


# Conditional on a buyer having actually committed to a date. This is a
# *conditional* probability, not a fresh attempt from scratch: someone who has
# just named a payment date is far more likely to pay than an unengaged debtor,
# which is the entire reason a promise-to-pay step is worth taking.
#
# Scoring the settlement with the full success_probability() formula instead
# would compound two ~30% draws into a ~9% outcome and make the negotiated path
# strictly worse than a plain reminder — which is an artefact of the model, not
# a property of receivables collection.
PROMISE_KEEP_FLOOR = 0.60
PROMISE_KEEP_RANGE = 0.30


def promise_kept(event: RevenueEvent, attempt_index: int) -> tuple[bool, float, dict]:
    propensity = event.sim_propensity if event.sim_propensity is not None else 0.5
    p = PROMISE_KEEP_FLOOR + PROMISE_KEEP_RANGE * propensity
    u = draw(event.case_key, attempt_index)
    return (u < p), p, {
        "model": "promise_kept",
        "keep_floor": PROMISE_KEEP_FLOOR,
        "customer_propensity": round(propensity, 3),
        "probability": round(p, 4),
        "random_draw": round(u, 4),
        "note": "Conditional on the buyer having committed to a date.",
    }
