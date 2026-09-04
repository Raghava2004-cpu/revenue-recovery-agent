"""
Central configuration.

Everything tunable lives here so the policy the agent ran under is a single
versioned string we can stamp onto every decision in the audit trail. If you
change a playbook, a compliance rule, or a stopping rule, bump POLICY_VERSION —
old audit rows keep pointing at the policy that actually produced them.
"""
import os
from zoneinfo import ZoneInfo

# Bumped whenever policy/ changes. Stamped on every audit row and decision.
POLICY_VERSION = "2026.09.04-r4"

# Merchant operates in India; all compliance windows are evaluated in IST.
MERCHANT_TZ = ZoneInfo("Asia/Kolkata")

# --- Razorpay -------------------------------------------------------------
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

# --- LLM ------------------------------------------------------------------
# The agent is fully functional with no key set: diagnosis falls back to the
# rule engine's UNKNOWN -> human escalation path, and message copy falls back
# to deterministic Hinglish templates. See app/ai/.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-opus-5")
LLM_ENABLED = bool(ANTHROPIC_API_KEY)

# --- Database -------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./revenue_recovery.db")

# --- Cost model -----------------------------------------------------------
# Per-contact costs in INR. Used to compute net recovered and ROI, so the
# agent is penalised for spraying messages rather than rewarded for volume.
CONTACT_COST_INR = {
    "sms": 0.20,
    "whatsapp": 0.35,
    "email": 0.02,
    "voice": 2.50,
    "system": 0.0,          # a gateway retry costs us nothing to attempt
    "internal_queue": 45.0,  # ~3 min of a support agent's time
}
