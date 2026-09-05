"""
Claude client wrapper.

Three things this module is responsible for, none of which belong in the
pipeline stages:

1. **Degrading instead of failing.** No API key, package missing, network down,
   rate limited, malformed response — every path returns `None` and the caller
   takes its deterministic branch. The agent must run end-to-end with zero AI
   configured; the LLM is an accuracy upgrade on a specific sub-problem, not a
   load-bearing dependency.

2. **Constraining the output.** Every call uses structured outputs
   (`output_config.format`), so the model returns schema-valid JSON or the call
   is treated as failed. The LLM never emits free text that anything downstream
   parses loosely.

3. **Counting the money.** Token usage and spend are accumulated per batch and
   surfaced on the dashboard, so "we used AI here" comes with a cost figure
   rather than a vibe.
"""
import json
import logging
import threading

from app.config import ANTHROPIC_API_KEY, LLM_MODEL

log = logging.getLogger(__name__)

# Claude Opus 5 list price, USD per million tokens.
_INPUT_USD_PER_MTOK = 5.00
_OUTPUT_USD_PER_MTOK = 25.00

_client = None
_client_error: str | None = None
_lock = threading.Lock()


class Usage:
    """Process-wide LLM usage accumulator, reset at the start of each batch."""

    def __init__(self):
        self.calls = 0
        self.failures = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def reset(self):
        self.__init__()
        _breaker.reset()

    def add(self, input_tokens: int, output_tokens: int):
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    @property
    def cost_usd(self) -> float:
        return (self.input_tokens / 1e6 * _INPUT_USD_PER_MTOK
                + self.output_tokens / 1e6 * _OUTPUT_USD_PER_MTOK)

    def snapshot(self) -> dict:
        return {
            "enabled": available() and not _breaker.is_open(),
            "model": LLM_MODEL if available() else None,
            "calls": self.calls,
            "failures": self.failures,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 4),
            "unavailable_reason": (
                None if (available() and not _breaker.is_open())
                else (_breaker.reason or _client_error
                      or "ANTHROPIC_API_KEY is not set")
            ),
            "circuit_open": _breaker.is_open(),
        }


usage = Usage()


class _Breaker:
    """Trips after `limit` consecutive failures; any success resets it."""

    limit = 3

    def __init__(self):
        self.consecutive_failures = 0
        self.reason = None

    def reset(self):
        self.consecutive_failures = 0
        self.reason = None

    def record_success(self):
        self.consecutive_failures = 0

    def record_failure(self, reason: str):
        self.consecutive_failures += 1
        if self.consecutive_failures == self.limit:
            self.reason = reason
            log.warning(
                "LLM circuit breaker OPEN after %d consecutive failures — the "
                "rest of this batch uses the deterministic path. Last error: %s",
                self.limit, reason,
            )

    def is_open(self) -> bool:
        return self.consecutive_failures >= self.limit


_breaker = _Breaker()


def available() -> bool:
    return bool(ANTHROPIC_API_KEY) and _client_error is None


def _get_client():
    global _client, _client_error
    if _client is not None or _client_error is not None:
        return _client
    with _lock:
        if _client is None and _client_error is None:
            try:
                import anthropic
                _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, max_retries=2)
            except ImportError:
                _client_error = "the `anthropic` package is not installed"
            except Exception as exc:                      # noqa: BLE001
                _client_error = f"client init failed: {exc}"
    return _client


def call_json(
    *, system: str, user: str, schema: dict, max_tokens: int = 1024,
    effort: str = "low",
) -> dict | None:
    """
    One structured-output call. Returns the parsed object, or None on any
    failure — callers must always have a non-LLM path.

    `effort="low"` is deliberate: these are short, well-specified classification
    and copywriting tasks where extra deliberation buys nothing and costs real
    money at batch volume.
    """
    if not ANTHROPIC_API_KEY:
        return None

    # Circuit breaker. A dead key, an exhausted credit balance or an outage
    # fails identically for every case in the batch, and each failure costs a
    # network round trip — 250 cases turned into ~70 doomed requests and made a
    # 16-second batch take minutes. After a few consecutive failures we stop
    # asking and let the deterministic path take over for the rest of the run.
    if _breaker.is_open():
        return None

    client = _get_client()
    if client is None:
        return None

    request = {
        "model": LLM_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "output_config": {
            "format": {"type": "json_schema", "schema": schema},
            "effort": effort,
        },
    }

    try:
        # Server-side fallbacks: if a policy classifier declines the request,
        # the API re-runs it on a fallback model inside the same call rather
        # than leaving a case undiagnosed. Retried without the beta below if
        # the account doesn't have it enabled.
        try:
            response = client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                **request,
            )
        except Exception as beta_exc:                      # noqa: BLE001
            log.debug("Falling back to the non-beta endpoint: %s", beta_exc)
            response = client.messages.create(**request)

        if response.stop_reason == "refusal":
            usage.failures += 1
            _breaker.record_failure("model declined the request")
            log.warning("LLM declined the request; using the deterministic path.")
            return None

        usage.add(response.usage.input_tokens, response.usage.output_tokens)
        _breaker.record_success()

        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            usage.failures += 1
            return None
        return json.loads(text)

    except Exception as exc:                               # noqa: BLE001
        usage.failures += 1
        _breaker.record_failure(str(exc)[:200])
        log.warning("LLM call failed (%s); using the deterministic path.", exc)
        return None
