"""
DIAGNOSE — raw failure signal to a root cause.

Deliberately a two-tier design, and the ordering is the point:

  Tier 1  Rule engine. Exact match on Razorpay's structured `error.reason`.
          Free, instant, deterministic, trivially auditable. Handles the large
          majority of real traffic. There is no version of "use an LLM here"
          that is better on any dimension that matters.

  Tier 2  LLM, only when tier 1 abstains — a generic reason with the actual
          cause buried in free text. Constrained to the same enum, gated on a
          confidence floor.

  Tier 3  UNKNOWN, which routes to a human. Not a failure mode; the correct
          answer when neither tier is confident.

The dashboard reports the tier split, so "where did AI actually earn its place"
is a number, not an assertion.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from app import audit
from app.ai import diagnose_llm
from app.models import RevenueEvent

# Razorpay `error.reason` (and our generator's equivalents) -> taxonomy code.
# Exact-match, not substring: substring matching on error strings is how you
# end up classifying "mandate_insufficient_funds" as plain INSUFFICIENT_FUNDS
# and then retrying a card that was never involved.
RULES: dict[str, str] = {
    "payment_failed": "INSUFFICIENT_FUNDS",
    "insufficient_funds": "INSUFFICIENT_FUNDS",
    "card_expired": "CARD_EXPIRED",
    "expired_card": "CARD_EXPIRED",
    "payment_declined_by_bank": "CARD_DECLINED_BY_BANK",
    "declined_by_issuer": "CARD_DECLINED_BY_BANK",
    "invalid_otp": "AUTH_FAILED_OTP",
    "authentication_failed": "AUTH_FAILED_OTP",
    "gateway_timeout": "GATEWAY_TIMEOUT",
    "gateway_error": "GATEWAY_TIMEOUT",
    "upi_collect_expired": "UPI_COLLECT_EXPIRED",
    "payment_blocked_risk": "RISK_BLOCKED",
    "risk_declined": "RISK_BLOCKED",

    "mandate_revoked": "MANDATE_REVOKED",
    "mandate_cancelled": "MANDATE_REVOKED",
    "mandate_limit_exceeded": "MANDATE_LIMIT_EXCEEDED",
    "mandate_insufficient_funds": "SUBSCRIPTION_INSUFFICIENT_FUNDS",

    "abandoned_at_payment_page": "CHECKOUT_ABANDONED_PAYMENT_PAGE",
    "abandoned_method_unavailable": "CHECKOUT_ABANDONED_METHOD_MISSING",

    "invoice_overdue_cashflow": "INVOICE_OVERDUE_CASHFLOW",
    "invoice_overdue_process": "INVOICE_OVERDUE_PROCESS",
    "invoice_disputed": "INVOICE_DISPUTED",
}


def _rule_engine(event: RevenueEvent) -> str | None:
    reason = (event.raw_error_reason or "").strip().lower()
    if not reason:
        return None
    code = RULES.get(reason)
    # A subscription failure whose reason maps to a card-level cause is a
    # mapping bug, not a diagnosis — abstain rather than mislabel.
    if code and event.event_type == "subscription_failed" and code == "INSUFFICIENT_FUNDS":
        return "SUBSCRIPTION_INSUFFICIENT_FUNDS"
    return code


def diagnose(db: Session, event: RevenueEvent, now: datetime) -> RevenueEvent:
    code = _rule_engine(event)
    source, confidence, rationale = "rule_engine", 1.0, ""

    if code:
        rationale = (f"Razorpay error.reason '{event.raw_error_reason}' maps "
                     f"deterministically to {code}.")
    else:
        llm = diagnose_llm.classify(
            event_type=event.event_type,
            payment_method=event.payment_method,
            error_code=event.raw_error_code,
            error_reason=event.raw_error_reason,
            error_description=event.raw_error_description,
        )
        if llm:
            code, confidence, rationale = llm
            source = "llm"
        else:
            code, confidence, source = "UNKNOWN", 0.0, "llm_unavailable"
            rationale = (
                "No structured error reason to match on, and the LLM fallback was "
                "unavailable or declined. Routing to a human rather than guessing."
            )

    event.root_cause = code
    event.diagnosis_source = source
    event.diagnosis_confidence = confidence
    event.diagnosis_rationale = rationale
    event.status = "in_recovery"
    db.commit()

    audit.record(
        db, event_id=event.id, stage="diagnose", occurred_at=now,
        decision=f"Diagnosed as {code} via {source} "
                 f"(confidence {confidence:.0%}). {rationale}",
        detail={
            "root_cause": code, "source": source, "confidence": confidence,
            "signal": {
                "error_code": event.raw_error_code,
                "error_reason": event.raw_error_reason,
                "error_description": event.raw_error_description,
            },
        },
    )
    return event
