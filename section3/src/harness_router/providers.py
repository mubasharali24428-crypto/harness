"""Provider execution for the Section 3 model router.

FIX-LOOP E1 (Mission 12, VT-2 verdict): the team BYO key posted to
``POST /api/v1/models/call`` now survives the REST seam and reaches this
layer. Two strictly separated modes:

**Real mode (a key is supplied)** — a genuine OpenAI-compatible
chat-completions call against ``DEEPSEEK_BASE_URL`` carrying
``Authorization: Bearer <key>``, honoring the resolved model name, the exact
messages, and ``ModelCallOpts``. Every failure maps to an honest typed error
(``InvalidApiKeyError`` / ``RateLimitExceededError`` /
``ModelUnavailableError``). Fabricated text is NEVER returned on this path.

**Simulated mode (no key, HARNESS_ENV=dev ONLY)** — the historical
mock-response / "Simulated response ..." behavior, kept as an explicit
development convenience. Outside an explicitly declared dev environment a
keyless call raises ``ModelUnavailableError`` (the API surfaces 503
PROVIDER_UNAVAILABLE) instead of pretending to answer.

Secrets law (repo hard rule): the raw key exists only inside the outbound
Authorization header. It never enters logs, exception messages, or
``ModelResult`` payloads (``_scrub`` is belt-and-braces on anything surfaced).
"""

import logging
import os
import time
from decimal import Decimal
from typing import Any

import httpx
from harness_contracts.signatures import ModelCallOpts, ModelResult

from harness_router.pricing import MODEL_PRICING_CATALOG, PricingEngine
from harness_router.provider_registry import (
    ANTHROPIC_VERSION,
    Endpoint,
    resolve_endpoint,
    wire_name,
)

logger = logging.getLogger(__name__)

# Kept for existing imports and the single-endpoint dev stack; the authoritative
# resolution now lives in provider_registry.resolve_endpoint(), which knows that
# only one URL used to be reachable no matter which model was named.
PROVIDER_BASE_URL_ENV = "DEEPSEEK_BASE_URL"
DEFAULT_PROVIDER_BASE_URL = "https://api.deepseek.com"

SIMULATED_DISABLED_MESSAGE = (
    "No provider credential was supplied and the simulated-provider path is "
    "disabled outside HARNESS_ENV=dev; attach the team key to reach the real provider."
)


class ModelRouterError(Exception):
    pass


class InvalidApiKeyError(ModelRouterError):
    """Raised when an API key is invalid or unauthorized."""

    pass


class RateLimitExceededError(ModelRouterError):
    """Raised when provider rate limits are exceeded."""

    pass


class ModelUnavailableError(ModelRouterError):
    """Raised when a model endpoint is unreachable or fails honestly."""

    pass


def wire_model_name(model: str) -> str:
    """Catalog name -> provider wire name.

    ``deepseek/deepseek-chat`` -> ``deepseek-chat``;
    ``local/qwen3:14b-ablated`` -> ``qwen3:14b-ablated`` (Ollama-ready).
    Bare names pass through unchanged.
    """
    return model.split("/", 1)[-1]


class ModelProviderClient:
    """Provider client executing LLM completions with fallback and error mapping."""

    def __init__(
        self,
        api_keys: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.api_keys = api_keys or {}
        self.mock_responses: dict[str, str] = {}
        self._transport = transport  # test seam: httpx.MockTransport et al.
        self.timeout_seconds = timeout_seconds

    def set_mock_response(self, prompt_substring: str, response_text: str) -> None:
        self.mock_responses[prompt_substring] = response_text

    def call(
        self,
        model: str,
        messages: list[dict[str, str]],
        opts: ModelCallOpts | None = None,
        api_key: str | None = None,
    ) -> ModelResult:
        opts = opts or ModelCallOpts()
        start_time = time.perf_counter()
        prompt_full = " ".join(m.get("content", "") for m in messages)

        # Deterministic error-mapping triggers pinned by several suites (S3
        # unit, S4, integration). Checked before ANY network or simulation
        # logic, with or without a key.
        if "INVALID_KEY_TRIGGER" in prompt_full:
            raise InvalidApiKeyError(f"Invalid API key for model provider '{model}'.")
        if "RATE_LIMIT_TRIGGER" in prompt_full:
            raise RateLimitExceededError(f"Rate limit exceeded on provider for model '{model}'.")

        if api_key:
            return self._call_real_provider(model, messages, opts, api_key, start_time)
        return self._call_simulated(model, prompt_full, start_time)

    # -- real provider path ---------------------------------------------------

    def _call_real_provider(
        self,
        model: str,
        messages: list[dict[str, str]],
        opts: ModelCallOpts,
        api_key: str,
        start_time: float,
    ) -> ModelResult:
        """Genuine completion from the provider that owns this model id.

        No synthesis anywhere: every failure raises, nothing fabricates text.
        """
        endpoint = resolve_endpoint(model)
        url = (
            gemini_url(endpoint, model)
            if endpoint.protocol == "gemini"
            else endpoint.url
        )
        payload = _request_payload(endpoint, model, messages, opts)
        headers = _request_headers(endpoint, api_key)

        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self._transport) as client:
                response = client.post(url, json=payload, headers=headers)
        except (httpx.HTTPError, TimeoutError) as exc:
            # Exception TYPE only — provider/transport text could echo secrets.
            raise ModelUnavailableError(
                self._scrub(
                    f"Model provider unreachable for '{model}': {type(exc).__name__}.", api_key
                )
            ) from exc

        if response.status_code in (401, 403):
            raise InvalidApiKeyError(
                self._scrub(
                    f"The provider rejected the supplied key for model '{model}' "
                    f"(HTTP {response.status_code}).",
                    api_key,
                )
            )
        if response.status_code == 429:
            raise RateLimitExceededError(
                self._scrub(f"Rate limit exceeded on provider for model '{model}'.", api_key)
            )
        if response.status_code >= 400:
            raise ModelUnavailableError(
                self._scrub(
                    f"Provider error for model '{model}' (HTTP {response.status_code}).",
                    api_key,
                )
            )

        try:
            text, usage = _parse_completion(endpoint, response.json())
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelUnavailableError(
                f"Provider returned a malformed completion for model '{model}'."
            ) from exc
        if not isinstance(text, str) or not text.strip():
            raise ModelUnavailableError(f"Provider returned no completion for model '{model}'.")

        input_tokens = int(usage.get("input") or max(1, len(messages_str(messages)) // 4))
        output_tokens = int(usage.get("output") or max(1, len(text) // 4))

        latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))
        return self._build_result(
            model=model,
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )

    # -- simulated (dev-only) path ---------------------------------------------

    def _call_simulated(self, model: str, prompt_full: str, start_time: float) -> ModelResult:
        """Keyless path: mock responses first (integration harnesses depend on
        them), then the synthesizer ONLY under explicit HARNESS_ENV=dev;
        otherwise an HONEST unavailable error."""
        response_text: str | None = None
        for k, v in self.mock_responses.items():
            if k in prompt_full:
                response_text = v
                break

        if response_text is None:
            if os.environ.get("HARNESS_ENV") != "dev":
                raise ModelUnavailableError(SIMULATED_DISABLED_MESSAGE)
            response_text = f"Simulated response from {model} for prompt: {prompt_full[:60]}..."

        latency_ms = int((time.perf_counter() - start_time) * 1000)
        if latency_ms == 0:
            latency_ms = 12

        input_tokens = max(1, len(prompt_full) // 4)
        output_tokens = max(1, len(response_text) // 4)
        return self._build_result(
            model=model,
            text=response_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )

    # -- shared helpers ---------------------------------------------------------

    @staticmethod
    def _build_result(
        model: str, text: str, input_tokens: int, output_tokens: int, latency_ms: int
    ) -> ModelResult:
        # H-023: cost math is exact Decimal internally (PricingEngine); the
        # frozen contracts/ field is a JSON number, so convert only at this
        # serialization edge. No float arithmetic ever touches money.
        #
        # An unpriced model bills 0 and says so in the log. The alternative was
        # PricingEngine's silent $0.50/$1.50 fallback, which would have charged
        # a real number for a model we have no price for — a free local run at
        # DeepSeek's rate, or Claude at a sixth of its rate. Zero is wrong too,
        # but it is visibly wrong and never invents spend.
        cost = priced_cost(model, input_tokens, output_tokens)
        return ModelResult(
            text=text,
            costUsd=float(cost),
            model=model,
            latencyMs=latency_ms,
        )

    @staticmethod
    def _scrub(text: str, secret: str | None) -> str:
        """Keep the raw key out of anything that could be surfaced/logged."""
        return text.replace(secret, "[redacted]") if secret else text


def messages_str(messages: list[dict[str, str]]) -> str:
    """Concatenated message content — token estimation input."""
    return " ".join(m.get("content", "") for m in messages)


# ---------------------------------------------------------------------------
# Wire protocols. One OpenAI-compatible shape is not enough for a registry of
# this size: Anthropic and Google both publish a native messages API whose
# request/response bodies differ, and pretending otherwise means silently
# dropping half the providers.
# ---------------------------------------------------------------------------


def _request_payload(
    endpoint: Endpoint, model: str, messages: list[dict[str, str]], opts: ModelCallOpts
) -> dict[str, Any]:
    """Build the request body for this endpoint's protocol."""
    wire = wire_name(model)
    extra: dict[str, Any] = {}
    if isinstance(opts.extra, dict):
        # S-03: the billed/recorded model must be the model actually called.
        extra = {k: v for k, v in opts.extra.items() if k not in ("model", "messages")}

    if endpoint.protocol == "anthropic":
        system = "\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
        turns = [m for m in messages if m.get("role") != "system"]
        payload: dict[str, Any] = {
            "model": wire,
            "messages": turns,
            # max_tokens is required by the Messages API; without one the call
            # would 400 on every Anthropic model.
            "max_tokens": opts.maxTokens or 4096,
        }
        if system:
            payload["system"] = system
        if opts.temperature is not None:
            payload["temperature"] = opts.temperature
        payload.update(extra)
        return payload

    if endpoint.protocol == "gemini":
        contents = [
            {
                "role": "model" if m.get("role") == "assistant" else "user",
                "parts": [{"text": m.get("content", "")}],
            }
            for m in messages
            if m.get("role") != "system"
        ]
        payload = {"contents": contents}
        config: dict[str, Any] = {}
        if opts.maxTokens:
            config["maxOutputTokens"] = opts.maxTokens
        if opts.temperature is not None:
            config["temperature"] = opts.temperature
        system = "\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if config:
            payload["generationConfig"] = config
        payload.update(extra)
        return payload

    payload = {"model": wire, "messages": messages}
    if opts.maxTokens:
        payload["max_tokens"] = opts.maxTokens
    if opts.temperature is not None:
        payload["temperature"] = opts.temperature
    payload.update(extra)
    return payload


def _request_headers(endpoint: Endpoint, api_key: str) -> dict[str, str]:
    """Auth per protocol. The raw key exists only here — never in a log, an
    exception message or a ModelResult."""
    if endpoint.protocol == "anthropic":
        return {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
    if endpoint.protocol == "gemini":
        return {"x-goog-api-key": api_key, "content-type": "application/json"}
    return {"Authorization": f"Bearer {api_key}", "content-type": "application/json"}


def gemini_url(endpoint: Endpoint, model: str) -> str:
    """Gemini addresses the model in the path, not the body."""
    base = endpoint.url.rstrip("/")
    if ":generateContent" in base:
        return base
    return f"{base}/v1beta/models/{wire_name(model)}:generateContent"


def _parse_completion(endpoint: Endpoint, data: Any) -> tuple[str, dict[str, int]]:
    """(text, {input, output} token counts) from a protocol-specific body."""
    if not isinstance(data, dict):
        raise TypeError("response body is not an object")

    if endpoint.protocol == "anthropic":
        blocks = data.get("content") or []
        text = "".join(
            b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
        )
        usage = data.get("usage") or {}
        return text, {
            "input": int(usage.get("input_tokens") or 0),
            "output": int(usage.get("output_tokens") or 0),
        }

    if endpoint.protocol == "gemini":
        candidates = data.get("candidates") or []
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        usage = data.get("usageMetadata") or {}
        return text, {
            "input": int(usage.get("promptTokenCount") or 0),
            "output": int(usage.get("candidatesTokenCount") or 0),
        }

    text = data["choices"][0]["message"]["content"]
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return text, {
        "input": int(usage.get("prompt_tokens") or 0),
        "output": int(usage.get("completion_tokens") or 0),
    }


def priced_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """Exact cost when we have a price for this model id; zero when we do not."""
    if model in MODEL_PRICING_CATALOG:
        return PricingEngine.calculate_cost(model, input_tokens, output_tokens)
    logger.warning(
        "model %r has no entry in the pricing catalog (%d known); billing 0 for this call",
        model,
        len(MODEL_PRICING_CATALOG),
    )
    return Decimal("0")
