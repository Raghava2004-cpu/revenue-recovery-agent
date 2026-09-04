"""
The vocabulary the whole agent shares.

The important part of this file is not the list of names — it's the *properties*
hung off each root cause. `auto_retry_safe`, `needs_new_instrument` and
`hard_stop` are what let the Decide stage reason about a failure instead of
looking it up in a flat table, and they're the reason retrying an expired card
three times is structurally impossible in this system.

Sources for the failure vocabulary: Razorpay's documented `error.reason` /
`error.source` / `error.step` fields on a failed payment
(razorpay.com/docs/errors/), plus the subscription mandate failure modes from
the eMandate/UPI Autopay docs.
"""
from dataclasses import dataclass


# --- Event types ----------------------------------------------------------
PAYMENT_FAILED = "payment_failed"
CHECKOUT_ABANDONED = "checkout_abandoned"
SUBSCRIPTION_FAILED = "subscription_failed"
INVOICE_OVERDUE = "invoice_overdue"

EVENT_TYPES = [PAYMENT_FAILED, CHECKOUT_ABANDONED, SUBSCRIPTION_FAILED, INVOICE_OVERDUE]


# --- Actions the agent is allowed to take ---------------------------------
# This is a closed set. The LLM can never invent an action; it can only ever
# influence *diagnosis* and *copy*. Money-moving decisions stay deterministic.
RETRY_SAME_INSTRUMENT = "retry_same_instrument"
RETRY_MANDATE = "retry_mandate"
REGENERATE_PAYMENT_LINK = "regenerate_payment_link"
OFFER_ALTERNATE_METHOD = "offer_alternate_method"
SEND_REMINDER = "send_reminder"
VOICE_CALL_HINGLISH = "voice_call_hinglish"
REQUEST_PROMISE_TO_PAY = "request_promise_to_pay"
ESCALATE_HUMAN = "escalate_human"
SUPPRESS = "suppress"

# Actions that touch the customer (subject to consent, quiet hours, frequency caps).
CONTACT_ACTIONS = {
    REGENERATE_PAYMENT_LINK, OFFER_ALTERNATE_METHOD, SEND_REMINDER,
    VOICE_CALL_HINGLISH, REQUEST_PROMISE_TO_PAY,
}

# Actions that move money without touching the customer (silent gateway retries).
SILENT_ACTIONS = {RETRY_SAME_INSTRUMENT, RETRY_MANDATE}


@dataclass(frozen=True)
class RootCause:
    code: str
    label: str
    family: str                  # payment | checkout | subscription | receivable
    auto_retry_safe: bool        # can we re-hit the same instrument unattended?
    needs_new_instrument: bool   # is the existing instrument permanently unusable?
    hard_stop: bool              # must never be auto-retried or dunned at all
    note: str                    # shown in the UI drill-down; explains the physics


def _rc(code, label, family, auto_retry_safe, needs_new_instrument, hard_stop, note):
    return RootCause(code, label, family, auto_retry_safe, needs_new_instrument, hard_stop, note)


ROOT_CAUSES: dict[str, RootCause] = {rc.code: rc for rc in [
    _rc("INSUFFICIENT_FUNDS", "Insufficient funds", "payment",
        auto_retry_safe=True, needs_new_instrument=False, hard_stop=False,
        note="The instrument works; the balance doesn't. Retry timing is the entire "
             "lever here — an immediate retry hits the same empty account."),

    _rc("CARD_EXPIRED", "Card expired", "payment",
        auto_retry_safe=False, needs_new_instrument=True, hard_stop=False,
        note="Deterministically unrecoverable on the same card. Any retry of the "
             "stored instrument is guaranteed to fail; only a new instrument works."),

    _rc("CARD_DECLINED_BY_BANK", "Declined by issuing bank", "payment",
        auto_retry_safe=False, needs_new_instrument=False, hard_stop=False,
        note="Issuer-side decline with no stated reason. Re-presenting the same card "
             "quickly can trip issuer velocity rules, so we switch rails instead."),

    _rc("AUTH_FAILED_OTP", "OTP / 3DS authentication failed", "payment",
        auto_retry_safe=False, needs_new_instrument=False, hard_stop=False,
        note="Customer was present and willing but the auth step broke. High intent, "
             "high recoverability — get them a fresh link fast, while intent is warm."),

    _rc("GATEWAY_TIMEOUT", "Gateway / network timeout", "payment",
        auto_retry_safe=True, needs_new_instrument=False, hard_stop=False,
        note="Transient infrastructure failure, nothing wrong with the customer or "
             "the instrument. A silent retry is correct and costs nothing."),

    _rc("UPI_COLLECT_EXPIRED", "UPI collect request expired", "payment",
        auto_retry_safe=False, needs_new_instrument=False, hard_stop=False,
        note="The collect request timed out unapproved. Re-sending a live intent "
             "link while the customer is still at their phone is what converts."),

    _rc("RISK_BLOCKED", "Blocked by risk / fraud rules", "payment",
        auto_retry_safe=False, needs_new_instrument=False, hard_stop=True,
        note="Declined by risk controls. Retrying is not merely useless — repeatedly "
             "re-presenting a risk-declined payment is exactly what card-scheme rules "
             "prohibit. The only compliant action is to stop and escalate."),

    _rc("MANDATE_REVOKED", "Mandate cancelled / revoked", "subscription",
        auto_retry_safe=False, needs_new_instrument=True, hard_stop=False,
        note="The customer withdrew debit authorisation. Charging again without a "
             "fresh mandate would be an unauthorised debit. Needs re-consent."),

    _rc("MANDATE_LIMIT_EXCEEDED", "Debit exceeds mandate limit", "subscription",
        auto_retry_safe=False, needs_new_instrument=False, hard_stop=False,
        note="The mandate is valid but capped below this amount. Retrying the same "
             "amount fails forever; the fix is a lower debit or a mandate amendment."),

    _rc("SUBSCRIPTION_INSUFFICIENT_FUNDS", "Auto-debit bounced — low balance", "subscription",
        auto_retry_safe=True, needs_new_instrument=False, hard_stop=False,
        note="Mandate is live, account was short on the debit date. This is the "
             "textbook case for a salary-cycle-aware retry."),

    _rc("CHECKOUT_ABANDONED_PAYMENT_PAGE", "Dropped at payment page", "checkout",
        auto_retry_safe=False, needs_new_instrument=False, hard_stop=False,
        note="Reached checkout and left without attempting. Intent decays by the "
             "hour, so speed of the first nudge dominates everything else."),

    _rc("CHECKOUT_ABANDONED_METHOD_MISSING", "Dropped — preferred method unavailable", "checkout",
        auto_retry_safe=False, needs_new_instrument=False, hard_stop=False,
        note="Left after browsing payment methods. Recovery means offering the rail "
             "they wanted, not re-sending the same checkout."),

    _rc("INVOICE_OVERDUE_CASHFLOW", "Overdue — buyer cashflow", "receivable",
        auto_retry_safe=False, needs_new_instrument=False, hard_stop=False,
        note="B2B buyer intends to pay but is short this cycle. A negotiated "
             "promise-to-pay recovers more than escalating pressure does."),

    _rc("INVOICE_OVERDUE_PROCESS", "Overdue — missing PO / wrong contact", "receivable",
        auto_retry_safe=False, needs_new_instrument=False, hard_stop=False,
        note="Nobody is refusing to pay; the invoice is stuck in the buyer's AP "
             "process. Reaching the right human resolves it quickly."),

    _rc("INVOICE_DISPUTED", "Overdue — invoice disputed", "receivable",
        auto_retry_safe=False, needs_new_instrument=False, hard_stop=True,
        note="The buyer contests the amount. Automated dunning against a live "
             "dispute is a legal and relationship risk. Humans only."),

    _rc("UNKNOWN", "Unclassified", "payment",
        auto_retry_safe=False, needs_new_instrument=False, hard_stop=True,
        note="The rule engine and the LLM both declined to classify this. We do not "
             "guess with money — it goes to a human."),
]}

ROOT_CAUSE_CODES = list(ROOT_CAUSES.keys())


def root_cause(code: str | None) -> RootCause:
    """Never raises — an unrecognised code degrades to UNKNOWN, which hard-stops."""
    return ROOT_CAUSES.get(code or "", ROOT_CAUSES["UNKNOWN"])


# --- Channels -------------------------------------------------------------
SMS, WHATSAPP, EMAIL, VOICE, SYSTEM, INTERNAL_QUEUE = (
    "sms", "whatsapp", "email", "voice", "system", "internal_queue"
)
