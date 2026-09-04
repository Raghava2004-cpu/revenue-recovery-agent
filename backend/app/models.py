"""
Schema.

Four tables:
  revenue_events    one at-risk case under one policy arm
  recovery_actions  every intervention attempt, including the ones we blocked
  audit_log         append-only, hash-chained record of every decision
  promises          promise-to-pay commitments extracted from B2B receivables

Note `arm` and `case_key` on RevenueEvent. Every generated case is inserted
twice — once under the agent policy, once under a naive baseline — sharing a
case_key and a latent customer propensity. That pairing is what makes
"money the agent recovered that the baseline would not have" a measurement
rather than a claim.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean, Text, ForeignKey, Index
)
from sqlalchemy.orm import relationship

from app.db import Base, engine


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RevenueEvent(Base):
    __tablename__ = "revenue_events"

    id = Column(Integer, primary_key=True, index=True)

    # --- experiment identity ---
    case_key = Column(String, index=True)     # shared by the agent/baseline twins
    arm = Column(String, index=True, default="agent")   # agent | baseline

    # --- what happened ---
    external_ref = Column(String, index=True)  # razorpay payment_id / invoice_id
    event_type = Column(String, index=True)
    amount = Column(Float)
    currency = Column(String, default="INR")

    raw_error_code = Column(String, nullable=True)
    raw_error_reason = Column(String, nullable=True)
    raw_error_description = Column(Text, nullable=True)
    payment_method = Column(String, nullable=True)   # card | upi | netbanking | emandate

    # --- who ---
    customer_id = Column(String, index=True)
    customer_name = Column(String, nullable=True)
    customer_contact = Column(String, nullable=True)
    customer_email = Column(String, nullable=True)
    segment = Column(String, nullable=True)    # new | returning | loyal | b2b
    language = Column(String, default="hinglish")  # hinglish | english

    # --- consent & compliance state ---
    do_not_contact = Column(Boolean, default=False)
    consent_whatsapp = Column(Boolean, default=True)
    consent_voice = Column(Boolean, default=True)
    consent_sms = Column(Boolean, default=True)

    # --- diagnosis ---
    root_cause = Column(String, nullable=True, index=True)
    diagnosis_source = Column(String, nullable=True)   # rule_engine | llm | llm_unavailable
    diagnosis_confidence = Column(Float, nullable=True)
    diagnosis_rationale = Column(Text, nullable=True)

    # --- lifecycle ---
    status = Column(String, default="detected", index=True)
    # detected | in_recovery | recovered | escalated | suppressed | exhausted
    attempt_count = Column(Integer, default=0)
    contact_count = Column(Integer, default=0)
    next_action_at = Column(DateTime, nullable=True)
    policy_version = Column(String, nullable=True)

    detected_at = Column(DateTime, default=utcnow, index=True)
    resolved_at = Column(DateTime, nullable=True)
    amount_recovered = Column(Float, default=0.0)
    cost_incurred = Column(Float, default=0.0)

    # --- simulation ground truth (never read by the agent) ---
    # The agent must not be able to see these; they exist only so the outcome
    # model can decide whether an attempt would really have worked. Asserted by
    # tests/test_no_oracle_leak.py.
    sim_propensity = Column(Float, nullable=True)      # latent willingness/ability
    sim_funds_at = Column(DateTime, nullable=True)     # when the balance recovers
    sim_reachable = Column(Boolean, default=True)      # is the contact detail live?

    actions = relationship("RecoveryAction", back_populates="event",
                           cascade="all, delete-orphan")
    promises = relationship("Promise", back_populates="event",
                            cascade="all, delete-orphan")


Index("ix_events_arm_status", RevenueEvent.arm, RevenueEvent.status)


class RecoveryAction(Base):
    __tablename__ = "recovery_actions"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("revenue_events.id"), index=True)

    attempt_no = Column(Integer)
    action_type = Column(String)
    channel = Column(String, nullable=True)
    message = Column(Text, nullable=True)
    message_source = Column(String, nullable=True)   # llm | template
    payment_link_url = Column(String, nullable=True)

    # blocked actions are recorded too — a compliance system that leaves no
    # trace of what it prevented can't be audited
    blocked = Column(Boolean, default=False)
    blocked_by_rule = Column(String, nullable=True)

    outcome = Column(String, default="pending")  # recovered | no_response | failed | blocked | pending
    amount_recovered = Column(Float, default=0.0)
    cost = Column(Float, default=0.0)
    success_probability = Column(Float, nullable=True)  # what the model gave this attempt

    scheduled_at = Column(DateTime, nullable=True)
    executed_at = Column(DateTime, default=utcnow)

    event = relationship("RevenueEvent", back_populates="actions")


class AuditLog(Base):
    """
    Append-only and hash-chained: each row commits to the hash of the row
    before it, so any edit or deletion after the fact breaks the chain and is
    detectable via GET /audit/verify. See app/audit.py.
    """
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("revenue_events.id"), nullable=True, index=True)

    seq = Column(Integer, index=True)          # position in the chain
    stage = Column(String)                     # detect|diagnose|decide|comply|act|observe|stop
    decision = Column(Text)                    # human-readable, shown in the UI
    detail = Column(Text, nullable=True)       # canonical JSON of the inputs/outputs
    policy_version = Column(String, nullable=True)
    occurred_at = Column(DateTime, default=utcnow)   # virtual (simulation) clock
    created_at = Column(DateTime, default=utcnow)    # wall clock

    prev_hash = Column(String, nullable=True)
    entry_hash = Column(String, index=True)


class Promise(Base):
    """Promise-to-pay commitments on overdue B2B receivables."""
    __tablename__ = "promises"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("revenue_events.id"), index=True)
    amount = Column(Float)
    promised_for = Column(DateTime)
    status = Column(String, default="open")   # open | kept | broken
    created_at = Column(DateTime, default=utcnow)
    settled_at = Column(DateTime, nullable=True)

    event = relationship("RevenueEvent", back_populates="promises")


def init_db():
    Base.metadata.create_all(bind=engine)
