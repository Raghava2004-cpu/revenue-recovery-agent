"""
Razorpay SDK wrapper, restricted to what the agent actually needs.

Docs: https://razorpay.com/docs/api/
Test cards/UPI that deterministically fail:
  https://razorpay.com/docs/payments/payments/test-card-upi-details/

Signature verification is the one thing here that must never be softened —
an unverified webhook is an unauthenticated write into your revenue ledger.
"""
import hashlib
import hmac
import logging

import razorpay

from app.config import (RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET,
                        RAZORPAY_WEBHOOK_SECRET)

log = logging.getLogger(__name__)

_client = None


def configured() -> bool:
    return bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)


def get_client():
    global _client
    if _client is None:
        if not configured():
            raise RuntimeError(
                "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not set. Generate test "
                "keys at dashboard.razorpay.com → Settings → API Keys."
            )
        _client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    return _client


def create_test_order(amount_paise: int, receipt: str, notes: dict | None = None):
    """Create a test-mode order, to stage a checkout that we can then fail."""
    return get_client().order.create({
        "amount": amount_paise, "currency": "INR",
        "receipt": receipt, "notes": notes or {},
    })


def create_recovery_payment_link(amount_paise: int, description: str,
                                 customer: dict, notes: dict | None = None):
    """
    The core recovery action. `notes.case_key` is what lets the
    `payment_link.paid` webhook find and close the case this link belongs to —
    without it, a real recovery can't be attributed back to the attempt that
    caused it.
    """
    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "description": description[:255],
        "customer": {k: v for k, v in customer.items() if v},
        "notify": {"sms": False, "email": False},  # the agent owns messaging
        "reminder_enable": False,                   # and owns the follow-up cadence
        "notes": notes or {},
    }
    return get_client().payment_link.create(payload)


def verify_webhook_signature(request_body: bytes, signature: str) -> bool:
    """HMAC-SHA256 over the raw body. Constant-time compare, never `==`."""
    if not RAZORPAY_WEBHOOK_SECRET:
        raise RuntimeError(
            "RAZORPAY_WEBHOOK_SECRET is not set; refusing to accept webhooks."
        )
    if not signature:
        return False
    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(), request_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
