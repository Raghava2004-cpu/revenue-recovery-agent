"""
LLM diagnosis — the ambiguous tail only.

The rule engine classifies the ~86% of failures where Razorpay gives us a clean
`error.reason`. It is faster, free, deterministic and perfectly auditable, and
replacing it with a model would be worse on every one of those axes. What the
rule engine cannot do is read a free-text description like

    "Txn rejected at issuer end — card validity period has elapsed"

and know that means CARD_EXPIRED. That's natural-language understanding over an
open-ended string, which is genuinely the right job for a model.

Three constraints keep it safe:

* The output is a **closed enum** enforced by a JSON schema. The model picks a
  label from our taxonomy; it cannot invent one, and it cannot choose an action.
* It must return a **confidence**. Below the floor we discard the answer and
  route to a human, because a guess about money is worse than an escalation.
* Its rationale is written verbatim to the audit trail, so a reviewer can see
  the model's reasoning next to the decision it influenced.
"""
from app import taxonomy as tx
from app.ai import client

# Below this we don't act on the model's answer.
CONFIDENCE_FLOOR = 0.70

_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string", "enum": tx.ROOT_CAUSE_CODES},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string", "maxLength": 300},
    },
    "required": ["root_cause", "confidence", "rationale"],
    "additionalProperties": False,
}

_SYSTEM = """You classify failed or at-risk payment events for an Indian \
payments platform into exactly one root cause from a fixed taxonomy.

You are the fallback for cases a deterministic rule engine could not classify \
from the structured error reason, so the signal you get is usually free text \
written by a gateway or a bank.

Rules:
- Choose the single code that the evidence actually supports.
- If the evidence is thin, ambiguous, or could plausibly fit several codes, \
return UNKNOWN with a low confidence. A downstream human reviews UNKNOWN cases; \
that is a good outcome. A confident wrong label causes a real customer to be \
charged, retried, or chased incorrectly, which is a bad outcome.
- Confidence is your genuine probability of being right, not a formality.
- Never infer a cause from the amount or the customer. Only the failure signal.

Taxonomy:
""" + "\n".join(
    f"- {c.code}: {c.label}. {c.note}" for c in tx.ROOT_CAUSES.values()
)


def classify(
    *, event_type: str, payment_method: str | None, error_code: str | None,
    error_reason: str | None, error_description: str | None,
) -> tuple[str, float, str] | None:
    """Returns (root_cause, confidence, rationale), or None to fall through."""
    if not client.available():
        return None

    user = (
        f"event_type: {event_type}\n"
        f"payment_method: {payment_method or 'unknown'}\n"
        f"error_code: {error_code or 'none'}\n"
        f"error_reason: {error_reason or 'none'}\n"
        f"error_description: {error_description or 'none'}"
    )

    result = client.call_json(system=_SYSTEM, user=user, schema=_SCHEMA,
                              max_tokens=512, effort="low")
    if not result:
        return None

    code = result.get("root_cause")
    confidence = float(result.get("confidence", 0.0))
    rationale = (result.get("rationale") or "").strip()

    if code not in tx.ROOT_CAUSES:
        return None

    if confidence < CONFIDENCE_FLOOR:
        return ("UNKNOWN", confidence,
                f"Model proposed {code} at {confidence:.0%} confidence, below the "
                f"{CONFIDENCE_FLOOR:.0%} floor — routed to a human instead. "
                f"Model's reasoning: {rationale}")

    return code, confidence, rationale
