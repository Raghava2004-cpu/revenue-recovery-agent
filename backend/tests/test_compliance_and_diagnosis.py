"""Compliance verdicts and the diagnosis tiers, tested directly."""
from datetime import datetime, timedelta, timezone

import pytest

from app import taxonomy as tx
from app.config import MERCHANT_TZ
from app.models import RevenueEvent
from app.pipeline.diagnose import _rule_engine
from app.policy import compliance


def _event(**kw):
    defaults = dict(
        case_key="k", arm="agent", amount=1500.0, root_cause="CARD_EXPIRED",
        event_type=tx.PAYMENT_FAILED, customer_id="cust_1", contact_count=0,
        do_not_contact=False, consent_whatsapp=True, consent_sms=True,
        consent_voice=True, segment="returning",
    )
    defaults.update(kw)
    return RevenueEvent(**defaults)


def _at(hour, day=15):
    """A UTC instant that is `hour` o'clock in merchant-local time."""
    local = datetime(2026, 9, day, hour, 0, tzinfo=MERCHANT_TZ)
    return local.astimezone(timezone.utc)


# --- quiet hours ------------------------------------------------------------

@pytest.mark.parametrize("hour", [22, 23, 2, 6, 8])
def test_quiet_hours_defer_rather_than_block(db, hour):
    """
    Deferring is the difference between a compliance control and lost revenue:
    the message still goes out, at 09:00.
    """
    verdict = compliance.evaluate(db, _event(), tx.REGENERATE_PAYMENT_LINK,
                                  tx.WHATSAPP, _at(hour))
    assert verdict.result == compliance.DEFER
    assert verdict.rule == "quiet_hours"
    assert verdict.retry_at.astimezone(MERCHANT_TZ).hour == compliance.QUIET_END_HOUR


@pytest.mark.parametrize("hour", [9, 13, 20])
def test_messaging_is_allowed_inside_the_window(db, hour):
    verdict = compliance.evaluate(db, _event(), tx.REGENERATE_PAYMENT_LINK,
                                  tx.WHATSAPP, _at(hour))
    assert verdict.allowed


def test_voice_window_is_narrower_than_messaging(db):
    assert compliance.evaluate(db, _event(), tx.VOICE_CALL_HINGLISH, tx.VOICE,
                               _at(20)).result == compliance.DEFER
    assert compliance.evaluate(db, _event(), tx.VOICE_CALL_HINGLISH, tx.VOICE,
                               _at(14)).allowed


# --- absolute blocks --------------------------------------------------------

def test_do_not_contact_blocks_in_every_arm(db):
    for arm, enforce in (("agent", True), ("baseline", False)):
        verdict = compliance.evaluate(
            db, _event(arm=arm, do_not_contact=True), tx.SEND_REMINDER,
            tx.SMS, _at(11), enforce=enforce)
        assert verdict.result == compliance.BLOCK
        assert verdict.rule == "do_not_contact"


def test_missing_consent_blocks_that_channel_only(db):
    e = _event(consent_whatsapp=False)
    assert compliance.evaluate(db, e, tx.SEND_REMINDER, tx.WHATSAPP,
                               _at(11)).result == compliance.BLOCK
    assert compliance.evaluate(db, e, tx.SEND_REMINDER, tx.SMS, _at(11)).allowed


def test_dead_instrument_retry_is_blocked(db):
    verdict = compliance.evaluate(db, _event(root_cause="CARD_EXPIRED"),
                                  tx.RETRY_SAME_INSTRUMENT, tx.SYSTEM, _at(11))
    assert verdict.result == compliance.BLOCK
    assert verdict.rule == "retry_on_dead_instrument"


def test_hard_stop_causes_block_all_autonomous_action(db):
    for code in ("RISK_BLOCKED", "INVOICE_DISPUTED", "UNKNOWN"):
        verdict = compliance.evaluate(db, _event(root_cause=code),
                                      tx.SEND_REMINDER, tx.SMS, _at(11))
        assert verdict.result == compliance.BLOCK
        assert verdict.rule == "hard_stop_root_cause"


def test_baseline_arm_downgrades_advisory_rules_but_not_the_legal_floor(db):
    """Quiet hours become advisory under the baseline; consent never does."""
    soft = compliance.evaluate(db, _event(arm="baseline"), tx.SEND_REMINDER,
                               tx.SMS, _at(23), enforce=False)
    assert soft.allowed and soft.rule == "quiet_hours"

    hard = compliance.evaluate(db, _event(arm="baseline", consent_sms=False),
                               tx.SEND_REMINDER, tx.SMS, _at(11), enforce=False)
    assert hard.result == compliance.BLOCK


def test_consumer_autonomy_ceiling_does_not_apply_to_receivables(db):
    """A ceiling that escalates every invoice forfeits the biggest balances."""
    consumer = _event(amount=90_000.0, root_cause="CARD_EXPIRED")
    assert compliance.evaluate(db, consumer, tx.SEND_REMINDER, tx.SMS,
                               _at(11)).rule == "high_value_needs_human"

    invoice = _event(amount=90_000.0, root_cause="INVOICE_OVERDUE_CASHFLOW",
                     event_type=tx.INVOICE_OVERDUE, segment="b2b")
    assert compliance.evaluate(db, invoice, tx.SEND_REMINDER, tx.EMAIL, _at(11)).allowed


# --- diagnosis --------------------------------------------------------------

@pytest.mark.parametrize("reason,expected", [
    ("card_expired", "CARD_EXPIRED"),
    ("invalid_otp", "AUTH_FAILED_OTP"),
    ("gateway_timeout", "GATEWAY_TIMEOUT"),
    ("payment_blocked_risk", "RISK_BLOCKED"),
    ("mandate_revoked", "MANDATE_REVOKED"),
    ("invoice_disputed", "INVOICE_DISPUTED"),
])
def test_rule_engine_classifies_structured_reasons(reason, expected):
    assert _rule_engine(_event(raw_error_reason=reason)) == expected


def test_rule_engine_abstains_on_free_text_only_signals():
    """Abstaining is what hands the case to the LLM tier instead of guessing."""
    assert _rule_engine(_event(raw_error_reason=None)) is None
    assert _rule_engine(_event(raw_error_reason="something we have never seen")) is None


def test_subscription_shortfall_is_not_confused_with_a_card_shortfall():
    """
    These need different interventions — a mandate re-presentment versus a new
    payment link — so collapsing them would send the wrong action.
    """
    e = _event(event_type=tx.SUBSCRIPTION_FAILED,
               raw_error_reason="mandate_insufficient_funds")
    assert _rule_engine(e) == "SUBSCRIPTION_INSUFFICIENT_FUNDS"


def test_diagnosis_degrades_to_human_review_without_an_llm(db):
    """No API key must mean 'escalate', never 'guess'."""
    from app.pipeline.diagnose import diagnose
    e = _event(raw_error_reason=None, raw_error_description="something opaque")
    db.add(e)
    db.commit()
    diagnose(db, e, now=datetime.now(timezone.utc))
    assert e.root_cause == "UNKNOWN"
    assert e.diagnosis_source == "llm_unavailable"
    assert tx.root_cause(e.root_cause).hard_stop is True
