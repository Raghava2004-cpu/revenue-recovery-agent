"""
Synthetic case generator.

Produces raw events shaped exactly like the Razorpay webhook payloads the live
path receives, so the pipeline downstream cannot tell a generated case from a
real one — the same detect/diagnose/decide/act code runs on both.

Two deliberate properties:

* **The true root cause is never emitted.** Only the raw signal is
  (`error_code`, `error_reason`, `error_description`). The Diagnose stage has to
  earn the classification. If the generator handed over the answer, the
  diagnosis accuracy figure on the dashboard would be meaningless.

* **~14% of cases are deliberately ambiguous**: a generic `payment_failed`
  reason with the real story buried in the free-text description, which is
  exactly what a merchant's log looks like in practice. The rule engine cannot
  resolve those, which is what gives the LLM fallback an honest job to do
  instead of a decorative one.
"""
import random
from datetime import datetime, timedelta, timezone

from faker import Faker

from app import taxonomy as tx

fake = Faker("en_IN")

# The fixed clock a seeded batch runs against. A Monday, mid-month, so the
# 14-day horizon spans two weekends and both the 1st-2nd and 15th salary-credit
# windows — the calendar conditions the policy actually has to handle.
REFERENCE_EPOCH = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)


# (weight, event_type, method, error_code, error_reason, description)
# `reason=None` means the signal is ambiguous and only free text is available.
SCENARIOS = [
    # --- payment failures (the bulk of a merchant's at-risk volume) ---
    (10, tx.PAYMENT_FAILED, "card", "BAD_REQUEST_ERROR", "payment_failed",
     "Your payment could not be completed due to insufficient funds in the account."),
    (7, tx.PAYMENT_FAILED, "card", "BAD_REQUEST_ERROR", "card_expired",
     "The card used for this payment has expired."),
    (6, tx.PAYMENT_FAILED, "card", "BAD_REQUEST_ERROR", "payment_declined_by_bank",
     "Payment was declined by the issuing bank without a stated reason."),
    (8, tx.PAYMENT_FAILED, "card", "BAD_REQUEST_ERROR", "invalid_otp",
     "The OTP entered was incorrect or the 3D Secure step timed out."),
    (6, tx.PAYMENT_FAILED, "upi", "GATEWAY_ERROR", "gateway_timeout",
     "The request to the upstream gateway timed out before a response."),
    (5, tx.PAYMENT_FAILED, "upi", "BAD_REQUEST_ERROR", "upi_collect_expired",
     "The UPI collect request expired without customer approval."),
    (2, tx.PAYMENT_FAILED, "card", "BAD_REQUEST_ERROR", "payment_blocked_risk",
     "Transaction blocked by risk engine rules."),

    # --- ambiguous: generic reason, real cause only in the free text ---
    (3, tx.PAYMENT_FAILED, "card", "BAD_REQUEST_ERROR", None,
     "Txn rejected at issuer end — card validity period has elapsed, customer "
     "must use a different card."),
    (3, tx.PAYMENT_FAILED, "card", "BAD_REQUEST_ERROR", None,
     "Customer's account did not have the required balance at the time of debit; "
     "bank returned a shortfall response."),
    (2, tx.PAYMENT_FAILED, "netbanking", "GATEWAY_ERROR", None,
     "Bank's netbanking page did not respond in time and the session was dropped "
     "mid-way. No debit occurred at the customer end."),
    (2, tx.PAYMENT_FAILED, "card", "BAD_REQUEST_ERROR", None,
     "Authentication could not be completed — customer did not finish the "
     "verification step sent by their bank."),
    (1, tx.PAYMENT_FAILED, "card", "BAD_REQUEST_ERROR", None,
     "Flagged during pre-authorisation screening; do not re-present."),

    # --- checkout abandonment ---
    (11, tx.CHECKOUT_ABANDONED, None, None, "abandoned_at_payment_page",
     "Customer reached the payment page and left without selecting a method."),
    (6, tx.CHECKOUT_ABANDONED, None, None, "abandoned_method_unavailable",
     "Customer browsed payment methods and exited; preferred method was not offered."),

    # --- subscriptions ---
    (5, tx.SUBSCRIPTION_FAILED, "emandate", "BAD_REQUEST_ERROR", "mandate_insufficient_funds",
     "Auto-debit bounced: balance below the debit amount on the mandate date."),
    (3, tx.SUBSCRIPTION_FAILED, "emandate", "BAD_REQUEST_ERROR", "mandate_revoked",
     "The customer cancelled the e-mandate with their bank."),
    (2, tx.SUBSCRIPTION_FAILED, "emandate", "BAD_REQUEST_ERROR", "mandate_limit_exceeded",
     "Debit amount exceeds the per-transaction limit registered on the mandate."),

    # --- B2B receivables ---
    (4, tx.INVOICE_OVERDUE, None, None, "invoice_overdue_cashflow",
     "Invoice past due date; buyer indicated a cashflow delay on the last call."),
    (3, tx.INVOICE_OVERDUE, None, None, "invoice_overdue_process",
     "Invoice past due date; buyer's AP team has not received a matching PO number."),
    (2, tx.INVOICE_OVERDUE, None, None, "invoice_disputed",
     "Buyer has formally disputed the invoiced amount pending reconciliation."),
]

AMOUNT_RANGES = {
    tx.PAYMENT_FAILED: (199, 8_999),
    tx.CHECKOUT_ABANDONED: (299, 12_999),
    tx.SUBSCRIPTION_FAILED: (99, 2_499),
    tx.INVOICE_OVERDUE: (18_000, 240_000),
}


def generate_batch(n: int = 60, seed: int | None = 42,
                   now: datetime | None = None) -> list[dict]:
    rng = random.Random(seed)
    if seed is not None:
        Faker.seed(seed)

    # A seeded batch is anchored to a fixed epoch, not to wall-clock time.
    #
    # Several policy behaviours are calendar-dependent — salary-cycle retries
    # target the 1st/2nd/7th/15th, receivables outreach skips weekends, quiet
    # hours depend on local time of day. Anchoring to `now` meant the same seed
    # produced different results on different days: the reported lift moved by
    # ~₹15,000 between two runs of `--seed 42` purely because the calendar had
    # advanced. Reproducibility is what makes the headline number checkable, so
    # the seed has to fix the clock as well as the dice.
    if now is None:
        now = (REFERENCE_EPOCH if seed is not None
               else datetime.now(timezone.utc))

    weights = [s[0] for s in SCENARIOS]
    cases: list[dict] = []

    for i in range(n):
        _, event_type, method, code, reason, description = rng.choices(
            SCENARIOS, weights=weights, k=1
        )[0]

        low, high = AMOUNT_RANGES[event_type]
        amount = round(rng.uniform(low, high), 2)

        is_b2b = event_type == tx.INVOICE_OVERDUE
        segment = "b2b" if is_b2b else rng.choices(
            ["new", "returning", "loyal"], weights=[0.35, 0.45, 0.20], k=1
        )[0]

        # Cases arrive spread over the past five days, at realistic hours —
        # including some inside the quiet window, so the compliance layer has
        # something real to defer.
        detected_at = now - timedelta(
            days=rng.uniform(0, 5), hours=rng.uniform(0, 23), minutes=rng.uniform(0, 59)
        )

        # Ground truth for the outcome model. Never read by the agent.
        funds_at = None
        if reason in ("payment_failed", "mandate_insufficient_funds") or (
            reason is None and "balance" in description
        ):
            funds_at = detected_at + timedelta(hours=rng.uniform(8, 110))

        cases.append({
            "case_key": f"case-{seed}-{i:04d}",
            "external_ref": f"pay_{rng.randrange(10**11, 10**12):012x}"
                            if event_type != tx.INVOICE_OVERDUE
                            else f"inv_{rng.randrange(10**10, 10**11):011x}",
            "event_type": event_type,
            "amount": amount,
            "payment_method": method,
            "raw_error_code": code,
            "raw_error_reason": reason,
            "raw_error_description": description,

            "customer_id": f"cust_{rng.randrange(10**7, 10**8)}",
            "customer_name": fake.company() if is_b2b else fake.name(),
            "customer_contact": f"+91{rng.randrange(70, 99)}{rng.randrange(10**7, 10**8)}",
            "customer_email": fake.company_email() if is_b2b else fake.email(),
            "segment": segment,
            "language": "english" if is_b2b else
                        rng.choices(["hinglish", "english"], weights=[0.75, 0.25], k=1)[0],

            "do_not_contact": rng.random() < 0.04,
            "consent_whatsapp": rng.random() > 0.12,
            "consent_sms": rng.random() > 0.05,
            "consent_voice": rng.random() > 0.25,

            "detected_at": detected_at,
            "sim_propensity": rng.random(),
            "sim_funds_at": funds_at,
            "sim_reachable": rng.random() > 0.07,
        })

    return cases
