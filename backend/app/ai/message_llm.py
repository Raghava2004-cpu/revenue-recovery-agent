"""
Recovery copy — Hinglish for retail, English for B2B.

Why a model is worth it here: the right message depends on *why* the payment
failed, *which* channel it's going out on, how many times we've already written,
and whether the customer reads Hinglish. That's a combinatorial space of tone
and content, and templates covering it either stay generic (and convert badly)
or multiply until nobody can maintain them.

Why it is still safe: generated copy is **validated before it is ever sent**.
The model cannot invent a discount, cannot threaten, cannot exceed the channel's
length budget, and cannot drop the payment link on an action that needs one. Any
violation and we fall back to the deterministic template — which is always
present, always correct, and is what runs when no API key is configured.

The voice variant returns a short spoken script; hand it to a TTS provider
(ElevenLabs / Sarvam, both of which do Hinglish well) for the audio channel.
"""
import re

from app import taxonomy as tx
from app.ai import client

LINK_TOKEN = "{payment_link}"

MAX_CHARS = {tx.SMS: 160, tx.WHATSAPP: 320, tx.EMAIL: 600, tx.VOICE: 420}

# Copy must never promise money we haven't authorised, or threaten.
BANNED = re.compile(
    r"\b(discount|cashback|free|refund|waive[dr]?|coupon|offer\s+code|legal\s+action|"
    r"lawyer|court|police|blacklist|penalt(y|ies))\b",
    re.IGNORECASE,
)

_SCHEMA = {
    "type": "object",
    "properties": {"message": {"type": "string", "minLength": 20, "maxLength": 600}},
    "required": ["message"],
    "additionalProperties": False,
}

_SYSTEM = f"""You write short payment-recovery messages for an Indian merchant.

Hard rules — a message breaking any of these is discarded:
- Never offer a discount, refund, waiver, cashback or any incentive. You have no \
authority to give anything away.
- Never threaten, shame, or imply legal or credit consequences.
- Never state anything you were not told (no invented dates, order details or names).
- If the caller says a payment link is included, put the literal token \
{LINK_TOKEN} exactly once where the link belongs. If not, never mention a link.
- Respect the character limit you are given.
- Write in the requested language. "Hinglish" means natural conversational \
Hindi-English code-mixing in Latin script, the way an Indian merchant actually \
messages customers — not formal Hindi, not Devanagari.

Tone: warm, direct, respectful of the customer's time. Say what happened, say \
what to do next, stop. One emoji at most, and only on WhatsApp.

You are given the diagnosed reason for the failure. Address that specific \
reason — a customer whose card expired should not receive the same message as \
one whose bank balance was short."""


# --- deterministic fallback -------------------------------------------------
# Reached when there is no API key, the call fails, or generated copy fails
# validation. These are complete and shippable, never placeholders.
_TEMPLATES = {
    ("CARD_EXPIRED", "hinglish"):
        "Hi {name}, aapka ₹{amount} ka payment nahi hua — card expire ho chuka hai. "
        "Naye card ya UPI se yahan complete karein: " + LINK_TOKEN,
    ("CARD_EXPIRED", "english"):
        "Hi {name}, your ₹{amount} payment didn't go through — the card on file has "
        "expired. Complete it with any other method here: " + LINK_TOKEN,

    ("INSUFFICIENT_FUNDS", "hinglish"):
        "Hi {name}, ₹{amount} ka payment balance kam hone ki wajah se nahi hua. "
        "Balance aane par yahan se pay kar dijiye: " + LINK_TOKEN,
    ("INSUFFICIENT_FUNDS", "english"):
        "Hi {name}, your ₹{amount} payment couldn't be completed due to insufficient "
        "balance. You can pay here whenever you're ready: " + LINK_TOKEN,

    ("AUTH_FAILED_OTP", "hinglish"):
        "Hi {name}, OTP verify nahi ho paya isliye ₹{amount} ka payment atak gaya. "
        "Dobara try karein, 30 second lagenge: " + LINK_TOKEN,
    ("AUTH_FAILED_OTP", "english"):
        "Hi {name}, your ₹{amount} payment stopped at the OTP step. It takes about "
        "30 seconds to finish here: " + LINK_TOKEN,

    ("CARD_DECLINED_BY_BANK", "hinglish"):
        "Hi {name}, aapke bank ne ₹{amount} ka card payment decline kar diya. "
        "UPI se try kijiye, turant ho jayega: " + LINK_TOKEN,
    ("CARD_DECLINED_BY_BANK", "english"):
        "Hi {name}, your bank declined the ₹{amount} card payment. UPI usually goes "
        "through instantly: " + LINK_TOKEN,

    ("UPI_COLLECT_EXPIRED", "hinglish"):
        "Hi {name}, UPI request expire ho gayi thi. Yeh naya link 10 minute valid "
        "hai — ₹{amount}: " + LINK_TOKEN,
    ("UPI_COLLECT_EXPIRED", "english"):
        "Hi {name}, your UPI request expired before approval. Here's a fresh link "
        "for ₹{amount}: " + LINK_TOKEN,

    ("MANDATE_REVOKED", "hinglish"):
        "Hi {name}, aapka auto-pay mandate cancel ho gaya hai, isliye ₹{amount} "
        "debit nahi hua. Dobara set up karein: " + LINK_TOKEN,
    ("MANDATE_REVOKED", "english"):
        "Hi {name}, your auto-pay mandate was cancelled, so the ₹{amount} debit "
        "didn't go through. You can set it up again here: " + LINK_TOKEN,

    ("MANDATE_LIMIT_EXCEEDED", "hinglish"):
        "Hi {name}, ₹{amount} aapke auto-pay limit se zyada hai. Is baar yahan se "
        "pay kar dijiye: " + LINK_TOKEN,
    ("MANDATE_LIMIT_EXCEEDED", "english"):
        "Hi {name}, ₹{amount} is above your auto-pay limit. You can pay this cycle "
        "here: " + LINK_TOKEN,

    ("SUBSCRIPTION_INSUFFICIENT_FUNDS", "hinglish"):
        "Hi {name}, ₹{amount} ka auto-debit balance kam hone se bounce hua. "
        "Balance rakhiye ya abhi pay kar dijiye: " + LINK_TOKEN,
    ("SUBSCRIPTION_INSUFFICIENT_FUNDS", "english"):
        "Hi {name}, your ₹{amount} auto-debit bounced due to low balance. Keep the "
        "balance topped up, or pay now: " + LINK_TOKEN,

    ("CHECKOUT_ABANDONED_PAYMENT_PAGE", "hinglish"):
        "Hi {name}, aapka ₹{amount} ka order abhi bhi reserved hai. Ek click mein "
        "complete karein: " + LINK_TOKEN,
    ("CHECKOUT_ABANDONED_PAYMENT_PAGE", "english"):
        "Hi {name}, your ₹{amount} order is still reserved. One click to finish: "
        + LINK_TOKEN,

    ("CHECKOUT_ABANDONED_METHOD_MISSING", "hinglish"):
        "Hi {name}, ab UPI aur netbanking dono available hain. ₹{amount} ka order "
        "yahan complete kijiye: " + LINK_TOKEN,
    ("CHECKOUT_ABANDONED_METHOD_MISSING", "english"):
        "Hi {name}, UPI and netbanking are both available now. Finish your ₹{amount} "
        "order here: " + LINK_TOKEN,

    ("INVOICE_OVERDUE_CASHFLOW", "english"):
        "Hello {name}, invoice for ₹{amount} is now past its due date. If the timing "
        "is difficult, reply with a date that works and we'll note it: " + LINK_TOKEN,
    ("INVOICE_OVERDUE_PROCESS", "english"):
        "Hello {name}, invoice for ₹{amount} is past due and appears to be pending a "
        "PO match with your AP team. Payment details here: " + LINK_TOKEN,
}

_GENERIC = {
    "hinglish": "Hi {name}, ₹{amount} ka payment pending hai. Yahan complete "
                "karein: " + LINK_TOKEN,
    "english": "Hi {name}, your ₹{amount} payment is still pending. You can "
               "complete it here: " + LINK_TOKEN,
}

_NO_LINK = {
    "hinglish": "Hi {name}, ₹{amount} ka payment hum dobara try kar rahe hain. "
                "Kuch karne ki zarurat nahi — ho jayega to confirm kar denge.",
    "english": "Hi {name}, we're retrying your ₹{amount} payment. Nothing needed "
               "from you — we'll confirm once it goes through.",
}


def _first_name(name: str | None) -> str:
    return (name or "there").split()[0]


def _template(root_cause: str, language: str, name: str, amount: float,
              needs_link: bool) -> str:
    if not needs_link:
        body = _NO_LINK[language if language in _NO_LINK else "english"]
    else:
        body = (_TEMPLATES.get((root_cause, language))
                or _TEMPLATES.get((root_cause, "english"))
                or _GENERIC.get(language, _GENERIC["english"]))
    # Token replacement rather than str.format: the templates also carry the
    # literal {payment_link} token, which format() would treat as a field.
    return (body.replace("{name}", _first_name(name))
                .replace("{amount}", f"{amount:,.0f}"))


def _valid(text: str, channel: str, needs_link: bool) -> bool:
    if not text or len(text) > MAX_CHARS.get(channel, 320):
        return False
    if BANNED.search(text):
        return False
    if needs_link and text.count(LINK_TOKEN) != 1:
        return False
    if not needs_link and LINK_TOKEN in text:
        return False
    return True


def build_message(
    *, action: str, channel: str, root_cause: str, language: str,
    customer_name: str | None, amount: float, attempt_no: int,
    event_type: str,
) -> tuple[str, str]:
    """
    Returns (message, source) where source is "llm" or "template".

    The template is computed first and always returned on any doubt, so this
    function cannot fail and cannot emit unvalidated model output.
    """
    needs_link = action in (
        tx.REGENERATE_PAYMENT_LINK, tx.OFFER_ALTERNATE_METHOD,
        tx.SEND_REMINDER, tx.REQUEST_PROMISE_TO_PAY,
    )
    fallback = _template(root_cause, language, customer_name, amount, needs_link)

    if not client.available():
        return fallback, "template"

    cause = tx.root_cause(root_cause)
    spoken = channel == tx.VOICE

    user = (
        f"Channel: {channel}{' (this is a spoken script for a phone call)' if spoken else ''}\n"
        f"Language: {language}\n"
        f"Character limit: {MAX_CHARS.get(channel, 320)}\n"
        f"Customer first name: {_first_name(customer_name)}\n"
        f"Amount: ₹{amount:,.0f}\n"
        f"Event: {event_type}\n"
        f"Diagnosed reason: {cause.label} — {cause.note}\n"
        f"Intervention: {action}\n"
        f"Payment link included: {'yes' if needs_link else 'no'}\n"
        f"This is contact number {attempt_no} for this case"
        f"{'; do not repeat the earlier phrasing, be shorter and more direct' if attempt_no > 1 else ''}.\n"
        + ("Write it to be read aloud: short sentences, no URLs, no emoji, and end "
           "by telling them the link is on its way by SMS.\n" if spoken else "")
        + "Return only the message."
    )

    result = client.call_json(system=_SYSTEM, user=user, schema=_SCHEMA,
                              max_tokens=400, effort="low")
    if not result:
        return fallback, "template"

    text = (result.get("message") or "").strip()
    if spoken:
        # A spoken script must not contain a link token; strip it if present.
        text = text.replace(LINK_TOKEN, "").strip()
        return (text, "llm") if _valid(text, channel, needs_link=False) else (fallback, "template")

    return (text, "llm") if _valid(text, channel, needs_link) else (fallback, "template")
