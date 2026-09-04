"""
The LLM tier, tested without an API key.

These are the paths that only execute when ANTHROPIC_API_KEY is set — which
means on a machine without a key they are exactly the paths that get shipped
untested and break live. The Claude call itself is the only thing stubbed;
everything around it (the confidence floor, the enum guard, the copy validator,
the fallback to templates) is the real code.
"""
from unittest.mock import patch

import pytest

from app import taxonomy as tx
from app.ai import client, diagnose_llm, message_llm


@pytest.fixture(autouse=True)
def _reset_usage():
    client.usage.reset()
    yield
    client.usage.reset()


def _llm(payload):
    """Pretend a key is configured and the API returned `payload`."""
    return (
        patch.object(client, "available", return_value=True),
        patch.object(client, "call_json", return_value=payload),
    )


def _run(payload, fn):
    avail, call = _llm(payload)
    with avail, call:
        return fn()


# --- diagnosis --------------------------------------------------------------

def test_confident_classification_is_accepted():
    result = _run(
        {"root_cause": "CARD_EXPIRED", "confidence": 0.93,
         "rationale": "The description says the card's validity period elapsed."},
        lambda: diagnose_llm.classify(
            event_type="payment_failed", payment_method="card",
            error_code="BAD_REQUEST_ERROR", error_reason=None,
            error_description="Txn rejected — card validity period has elapsed.",
        ),
    )
    code, confidence, rationale = result
    assert code == "CARD_EXPIRED"
    assert confidence == 0.93
    assert "validity" in rationale


def test_low_confidence_is_overridden_to_unknown():
    """
    Below the floor the model's answer is discarded, not acted on. UNKNOWN is a
    hard-stop cause, so the case goes to a human instead of driving a retry.
    """
    code, confidence, rationale = _run(
        {"root_cause": "MANDATE_REVOKED", "confidence": 0.41, "rationale": "Not sure."},
        lambda: diagnose_llm.classify(
            event_type="subscription_failed", payment_method="emandate",
            error_code=None, error_reason=None, error_description="Debit did not go through.",
        ),
    )
    assert code == "UNKNOWN"
    assert confidence == 0.41
    assert "below the" in rationale and "MANDATE_REVOKED" in rationale
    assert tx.root_cause(code).hard_stop is True


def test_a_label_outside_the_taxonomy_is_rejected():
    """A hallucinated code must not reach the policy engine."""
    assert _run(
        {"root_cause": "CUSTOMER_CHANGED_THEIR_MIND", "confidence": 0.99,
         "rationale": "Invented."},
        lambda: diagnose_llm.classify(
            event_type="payment_failed", payment_method="card", error_code=None,
            error_reason=None, error_description="Something happened.",
        ),
    ) is None


def test_a_failed_call_falls_through_rather_than_raising():
    assert _run(None, lambda: diagnose_llm.classify(
        event_type="payment_failed", payment_method="card", error_code=None,
        error_reason=None, error_description="…",
    )) is None


def test_diagnose_stage_records_the_llm_as_the_source(db):
    """End to end: an unmatched reason routes to the LLM and is recorded as such."""
    from datetime import datetime, timezone
    from app.models import RevenueEvent
    from app.pipeline.diagnose import diagnose

    event = RevenueEvent(
        case_key="k", arm="agent", event_type="payment_failed", amount=1200.0,
        customer_id="c1", raw_error_reason=None,
        raw_error_description="Card validity period has elapsed.",
    )
    db.add(event)
    db.commit()

    avail, call = _llm({"root_cause": "CARD_EXPIRED", "confidence": 0.9,
                        "rationale": "Validity elapsed."})
    with avail, call:
        diagnose(db, event, now=datetime.now(timezone.utc))

    assert event.root_cause == "CARD_EXPIRED"
    assert event.diagnosis_source == "llm"
    assert event.diagnosis_confidence == 0.9


# --- message generation -----------------------------------------------------

def _build(**over):
    kw = dict(action=tx.REGENERATE_PAYMENT_LINK, channel=tx.WHATSAPP,
              root_cause="CARD_EXPIRED", language="hinglish",
              customer_name="Priya Sharma", amount=2499.0, attempt_no=1,
              event_type="payment_failed")
    kw.update(over)
    return message_llm.build_message(**kw)


def test_valid_generated_copy_is_used():
    text, source = _run(
        {"message": "Hi Priya, aapka ₹2,499 ka payment nahi hua — card expire ho "
                    "gaya hai. Naye card se yahan pay karein: {payment_link}"},
        _build,
    )
    assert source == "llm"
    assert text.count(message_llm.LINK_TOKEN) == 1


@pytest.mark.parametrize("bad,why", [
    ("Hi Priya, get 20% discount if you pay now: {payment_link}", "offers a discount"),
    ("Pay now or we will take legal action: {payment_link}", "threatens"),
    ("Hi Priya, your payment failed. Please retry.", "drops the payment link"),
    ("x" * 400 + " {payment_link}", "exceeds the channel length budget"),
])
def test_unsafe_generated_copy_falls_back_to_the_template(bad, why):
    """
    The model cannot invent an incentive, threaten, or drop the link — whatever
    it returns, the customer only ever receives validated copy.
    """
    text, source = _run({"message": bad}, _build)
    assert source == "template", f"accepted copy that {why}"
    assert message_llm.LINK_TOKEN in text
    assert "discount" not in text.lower()


def test_no_key_means_templates_not_an_error():
    with patch.object(client, "available", return_value=False):
        text, source = _build()
    assert source == "template"
    assert "Priya" in text and "2,499" in text


def test_voice_copy_never_carries_a_link_token():
    """A link token read aloud down a phone line is nonsense."""
    text, source = _run(
        {"message": "Namaste Priya ji, aapka do hazaar chaar sau ninyanve rupaye ka "
                    "payment nahi hua. Link SMS par bhej rahe hain."},
        lambda: _build(action=tx.VOICE_CALL_HINGLISH, channel=tx.VOICE),
    )
    assert source == "llm"
    assert message_llm.LINK_TOKEN not in text


def test_usage_and_cost_are_tracked():
    """Spend has to be visible on the dashboard, not invisible."""
    client.usage.add(1200, 300)
    snap = client.usage.snapshot()
    assert snap["calls"] == 1
    assert snap["cost_usd"] == pytest.approx(1200 / 1e6 * 5 + 300 / 1e6 * 25)
