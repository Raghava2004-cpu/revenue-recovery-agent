"""
Verify the LLM tier before you rely on it.

    python check_llm.py

Makes two real API calls — one diagnosis, one message — and prints what came
back plus what it cost. Run this after setting ANTHROPIC_API_KEY and before
recording a demo, so you find a bad key on your terms rather than on camera.

Exit code is 0 only if both calls succeeded.
"""
import sys

from dotenv import load_dotenv

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

load_dotenv()

from app.ai import client, diagnose_llm, message_llm   # noqa: E402
from app.config import LLM_MODEL                        # noqa: E402

# A real free-text failure the rule engine deliberately abstains on: the reason
# field is absent and the actual cause is buried in prose.
AMBIGUOUS = {
    "event_type": "payment_failed",
    "payment_method": "card",
    "error_code": "BAD_REQUEST_ERROR",
    "error_reason": None,
    "error_description": (
        "Txn rejected at issuer end — card validity period has elapsed, "
        "customer must use a different card."
    ),
}
EXPECTED = "CARD_EXPIRED"


def main() -> int:
    print()
    if not client.available():
        print("  LLM tier is OFF.")
        print(f"  Reason: {client.usage.snapshot()['unavailable_reason']}")
        print("\n  Set ANTHROPIC_API_KEY in backend/.env, then run this again.")
        print("  Get a key at https://console.anthropic.com -> API Keys\n")
        return 1

    print(f"  Model: {LLM_MODEL}\n")
    ok = True

    # --- 1. diagnosis --------------------------------------------------
    print("  [1/2] Diagnosing an ambiguous failure the rule engine can't match")
    print(f"        signal: \"{AMBIGUOUS['error_description'][:66]}…\"")
    result = diagnose_llm.classify(**AMBIGUOUS)

    if result is None:
        print("        FAILED — no usable response. Check the key and network.\n")
        ok = False
    else:
        code, confidence, rationale = result
        verdict = "correct" if code == EXPECTED else f"expected {EXPECTED}"
        print(f"        -> {code} at {confidence:.0%} confidence ({verdict})")
        print(f"        rationale: {rationale[:96]}")
        if code != EXPECTED:
            print("        NOTE: not wrong exactly — but check the taxonomy prompt.")
        print()

    # --- 2. message generation -----------------------------------------
    print("  [2/2] Generating Hinglish recovery copy")
    text, source = message_llm.build_message(
        action="regenerate_payment_link", channel="whatsapp",
        root_cause="CARD_EXPIRED", language="hinglish",
        customer_name="Priya Sharma", amount=2499.0,
        attempt_no=1, event_type="payment_failed",
    )
    print(f"        source: {source}")
    print(f"        \"{text}\"")
    if source != "llm":
        print("        FELL BACK to the template — the call failed, or the")
        print("        generated copy failed validation (banned words, length,")
        print("        or a missing payment-link token).")
        ok = False
    print()

    u = client.usage.snapshot()
    print(f"  Calls: {u['calls']}  failures: {u['failures']}  "
          f"tokens: {u['input_tokens']} in / {u['output_tokens']} out  "
          f"cost: ${u['cost_usd']:.4f}")

    if ok:
        print("\n  LLM tier is working. Run a batch and the diagnosis panel will")
        print("  show a real LLM percentage instead of 0%.\n")
    else:
        print("\n  LLM tier is NOT fully working. The agent still runs — it")
        print("  degrades to the rule engine plus human escalation.\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
