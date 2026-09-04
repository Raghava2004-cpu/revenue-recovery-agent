"""
DETECT — normalise a raw signal into a case.

This stage decides nothing. It exists so that a Razorpay webhook and a generated
batch case become the same object before anything downstream looks at them,
which is why the live path and the demo path share 100% of the pipeline code.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from app import audit
from app.config import POLICY_VERSION
from app.models import RevenueEvent


def detect(db: Session, raw: dict, arm: str = "agent",
           now: datetime | None = None) -> RevenueEvent:
    detected_at = raw.get("detected_at") or now

    event = RevenueEvent(
        case_key=raw.get("case_key") or raw["external_ref"],
        arm=arm,
        external_ref=raw.get("external_ref"),
        event_type=raw["event_type"],
        amount=float(raw.get("amount") or 0.0),
        currency=raw.get("currency", "INR"),
        payment_method=raw.get("payment_method"),
        raw_error_code=raw.get("raw_error_code"),
        raw_error_reason=raw.get("raw_error_reason"),
        raw_error_description=raw.get("raw_error_description"),

        customer_id=raw.get("customer_id"),
        customer_name=raw.get("customer_name"),
        customer_contact=raw.get("customer_contact"),
        customer_email=raw.get("customer_email"),
        segment=raw.get("segment"),
        language=raw.get("language", "hinglish"),

        do_not_contact=bool(raw.get("do_not_contact", False)),
        consent_whatsapp=bool(raw.get("consent_whatsapp", True)),
        consent_sms=bool(raw.get("consent_sms", True)),
        consent_voice=bool(raw.get("consent_voice", True)),

        status="detected",
        policy_version=POLICY_VERSION,
        detected_at=detected_at,

        sim_propensity=raw.get("sim_propensity"),
        sim_funds_at=raw.get("sim_funds_at"),
        sim_reachable=raw.get("sim_reachable", True),
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    audit.record(
        db, event_id=event.id, stage="detect", occurred_at=detected_at,
        decision=(
            f"Revenue at risk detected: {event.event_type} worth "
            f"₹{event.amount:,.2f} from {event.customer_id}."
        ),
        detail={
            "arm": arm,
            "external_ref": event.external_ref,
            "payment_method": event.payment_method,
            "error_code": event.raw_error_code,
            "error_reason": event.raw_error_reason,
            "error_description": event.raw_error_description,
            "segment": event.segment,
        },
    )
    return event
