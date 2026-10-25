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

import os
import time
from typing import Any

import httpx
from harness_contracts.signatures import ModelCallOpts, ModelResult

from harness_router.pricing import PricingEngine

# OpenAI-compatible endpoint. DEEPSEEK_BASE_URL also serves local Ollama
# (AGENTS.md / catalog.py); unset falls back to DeepSeek's official API.
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
        """Genuine completion from the OpenAI-compatible endpoint.

        No synthesis anywhere: every failure raises, nothing fabricates text.
        """
        base_url = os.environ.get(PROVIDER_BASE_URL_ENV, DEFAULT_PROVIDER_BASE_URL).rstrip("/")
        url = f"{base_url}/chat/completions"

        payload: dict[str, Any] = {"model": wire_model_name(model), "messages": messages}
        if opts.maxTokens:
            payload["max_tokens"] = opts.maxTokens
        if opts.temperature is not None:
            payload["temperature"] = opts.temperature
        if isinstance(opts.extra, dict):
            # S-03: the billed/recorded model must be the model actually called.
            payload.update({k: v for k, v in opts.extra.items() if k not in ("model", "messages")})

        headers = {"Authorization": f"Bearer {api_key}"}

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
            data = response.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelUnavailableError(
                f"Provider returned a malformed completion for model '{model}'."
            ) from exc
        if not isinstance(text, str) or not text.strip():
            raise ModelUnavailableError(f"Provider returned no completion for model '{model}'.")

        usage = data.get("usage") if isinstance(data, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        input_tokens = int(usage.get("prompt_tokens") or max(1, len(messages_str(messages)) // 4))
        output_tokens = int(usage.get("completion_tokens") or max(1, len(text) // 4))

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
        cost = PricingEngine.calculate_cost(model, input_tokens, output_tokens)
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
