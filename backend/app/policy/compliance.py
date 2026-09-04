"""
Compliance guardrails — evaluated before every single intervention.

Each rule returns one of three verdicts:

  ALLOW  proceed
  DEFER  legal to do, illegal *right now* — reschedule to the next open window
  BLOCK  never do this; record why and stop

DEFER matters more than it looks. A quiet-hours rule that blocks turns a
compliance control into lost revenue; a quiet-hours rule that defers keeps the
money and the rule. Every deferral and block is written to the audit trail, so
the trail shows not just what the agent did but what it declined to do.

The baseline arm runs the same evaluation but enforces only the legal floor
(do-not-contact and per-channel consent). Everything else it would have
violated is recorded rather than enforced — that's where the compliance
comparison on the dashboard comes from.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app import taxonomy as tx
from app.config import MERCHANT_TZ
from app.models import RecoveryAction, RevenueEvent

ALLOW, DEFER, BLOCK = "allow", "defer", "block"

# --- windows, in merchant-local time ---
QUIET_START_HOUR = 21        # 21:00 IST — no messaging after this
QUIET_END_HOUR = 9           # 09:00 IST — messaging resumes
VOICE_START_HOUR, VOICE_END_HOUR = 10, 19
BUSINESS_START_HOUR, BUSINESS_END_HOUR = 9, 18

# --- contact budget, per customer ---
MIN_GAP_BETWEEN_CONTACTS = timedelta(hours=18)
MAX_CONTACTS_PER_7_DAYS = 3

# Above this, an autonomous outbound contact needs a human to sign off first.
HUMAN_APPROVAL_THRESHOLD_INR = 50_000.0


@dataclass
class Verdict:
    result: str
    rule: str | None = None
    reason: str = ""
    retry_at: datetime | None = None   # populated on DEFER

    @property
    def allowed(self) -> bool:
        return self.result == ALLOW


def _local(ts: datetime) -> datetime:
    return ts.astimezone(MERCHANT_TZ)


def _next_open(ts: datetime, start_hour: int) -> datetime:
    """The next occurrence of start_hour local time, strictly after ts."""
    local = _local(ts)
    candidate = local.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(ts.tzinfo)


def _next_business_open(ts: datetime) -> datetime:
    candidate = _next_open(ts, BUSINESS_START_HOUR)
    # Skip Sat/Sun for receivables chasing.
    while _local(candidate).weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def evaluate(
    db: Session,
    event: RevenueEvent,
    action: str,
    channel: str,
    now: datetime,
    timing: str | None = None,
    enforce: bool = True,
) -> Verdict:
    """
    `enforce=False` (baseline arm) still blocks on the legal floor but downgrades
    the remaining rules to advisory, returning ALLOW with the rule that *would*
    have fired recorded in `.rule`.
    """

    def soft(verdict: Verdict) -> Verdict:
        if enforce:
            return verdict
        return Verdict(ALLOW, rule=verdict.rule,
                       reason=f"[not enforced under baseline policy] {verdict.reason}")

    cause = tx.root_cause(event.root_cause)

    # =====================================================================
    # LEGAL FLOOR — evaluated first, unconditionally, in every arm.
    #
    # Order matters here for a reason that cost real correctness once: these
    # checks originally sat below the advisory rules, and under the baseline
    # arm an advisory rule returned ALLOW and short-circuited *past* them, so
    # do-not-contact customers were messaged. A rule that must never be
    # softened cannot sit downstream of one that can.
    # =====================================================================
    if action in tx.CONTACT_ACTIONS:
        if event.do_not_contact:
            return Verdict(BLOCK, "do_not_contact",
                           "Customer is on the do-not-contact list.")

        consent = {
            tx.WHATSAPP: event.consent_whatsapp,
            tx.SMS: event.consent_sms,
            tx.VOICE: event.consent_voice,
            tx.EMAIL: True,
        }.get(channel, True)
        if not consent:
            return Verdict(BLOCK, "channel_consent",
                           f"No consent on record for {channel}.")

    # =====================================================================
    # ADVISORY RULES — enforced for the agent, recorded-only for the baseline.
    # =====================================================================

    # --- 1. Hard-stop causes: nothing autonomous, ever. -------------------
    if cause.hard_stop and action not in (tx.ESCALATE_HUMAN, tx.SUPPRESS):
        return soft(Verdict(
            BLOCK, "hard_stop_root_cause",
            f"{cause.label} may not be actioned autonomously. {cause.note}",
        ))

    # --- 2. Never re-present an instrument that cannot succeed. -----------
    if action in (tx.RETRY_SAME_INSTRUMENT, tx.RETRY_MANDATE):
        if cause.needs_new_instrument:
            return soft(Verdict(
                BLOCK, "retry_on_dead_instrument",
                f"Re-presenting is futile and abusive under {cause.label}: the "
                f"existing instrument or authorisation cannot succeed.",
            ))
        if not cause.auto_retry_safe:
            return soft(Verdict(
                BLOCK, "retry_not_safe",
                f"{cause.label} is not safe to auto-retry; repeated presentment "
                f"risks issuer velocity blocks.",
            ))

    # Non-contact actions clear the remaining (customer-facing) rules.
    if action not in tx.CONTACT_ACTIONS:
        return Verdict(ALLOW)

    # --- 3. High-value autonomy ceiling. ----------------------------------
    # Scoped to consumer collections. A B2B receivable is a contractually due
    # invoice against a known counterparty with an AR process already expecting
    # the reminder — applying a consumer autonomy ceiling to it would escalate
    # every invoice to a human and forfeit the largest balances on the book,
    # which is the opposite of the control's intent.
    if (cause.family != "receivable"
            and event.amount > HUMAN_APPROVAL_THRESHOLD_INR):
        return soft(Verdict(
            BLOCK, "high_value_needs_human",
            f"₹{event.amount:,.0f} exceeds the ₹{HUMAN_APPROVAL_THRESHOLD_INR:,.0f} "
            f"consumer autonomy ceiling; routing to a human for approval.",
        ))

    # --- 6. Frequency caps. ------------------------------------------------
    recent = (
        db.query(RecoveryAction)
        .join(RevenueEvent, RecoveryAction.event_id == RevenueEvent.id)
        .filter(
            RevenueEvent.customer_id == event.customer_id,
            RevenueEvent.arm == event.arm,
            RecoveryAction.blocked.is_(False),
            RecoveryAction.channel.in_([tx.SMS, tx.WHATSAPP, tx.VOICE, tx.EMAIL]),
        )
        .order_by(RecoveryAction.executed_at.desc())
        .limit(10)
        .all()
    )
    contacts_7d = [a for a in recent
                   if a.executed_at and (now - _aware(a.executed_at)) <= timedelta(days=7)]

    if len(contacts_7d) >= MAX_CONTACTS_PER_7_DAYS:
        return soft(Verdict(
            BLOCK, "frequency_cap_7d",
            f"Customer already received {len(contacts_7d)} contacts in 7 days "
            f"(cap {MAX_CONTACTS_PER_7_DAYS}).",
        ))

    if contacts_7d:
        last = _aware(contacts_7d[0].executed_at)
        if now - last < MIN_GAP_BETWEEN_CONTACTS:
            return soft(Verdict(
                DEFER, "min_contact_gap",
                f"Last contact was {(now - last).total_seconds() / 3600:.1f}h ago; "
                f"minimum gap is {MIN_GAP_BETWEEN_CONTACTS.total_seconds() / 3600:.0f}h.",
                retry_at=last + MIN_GAP_BETWEEN_CONTACTS,
            ))

    # --- 7. Quiet hours. ---------------------------------------------------
    hour = _local(now).hour
    if hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR:
        return soft(Verdict(
            DEFER, "quiet_hours",
            f"{_local(now):%H:%M} IST falls in the {QUIET_START_HOUR}:00–"
            f"{QUIET_END_HOUR}:00 quiet window.",
            retry_at=_next_open(now, QUIET_END_HOUR),
        ))

    # --- 8. Voice calls have a narrower window than messaging. -------------
    if channel == tx.VOICE and not (VOICE_START_HOUR <= hour < VOICE_END_HOUR):
        return soft(Verdict(
            DEFER, "voice_calling_window",
            f"Voice calls are restricted to {VOICE_START_HOUR}:00–{VOICE_END_HOUR}:00 IST.",
            retry_at=_next_open(now, VOICE_START_HOUR),
        ))

    # --- 9. Business-hours-only steps (B2B receivables). -------------------
    if timing == "business_hours":
        local = _local(now)
        if local.weekday() >= 5 or not (BUSINESS_START_HOUR <= local.hour < BUSINESS_END_HOUR):
            return soft(Verdict(
                DEFER, "business_hours_only",
                "Receivables outreach is restricted to weekday business hours.",
                retry_at=_next_business_open(now),
            ))

    return Verdict(ALLOW)


def _aware(ts: datetime) -> datetime:
    """SQLite hands back naive datetimes; treat them as the UTC we wrote."""
    from datetime import timezone
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
