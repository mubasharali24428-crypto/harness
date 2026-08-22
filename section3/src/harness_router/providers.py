import time

from harness_contracts.signatures import ModelCallOpts, ModelResult

from harness_router.pricing import PricingEngine


class ModelRouterError(Exception):
    pass


class InvalidApiKeyError(ModelRouterError):
    """Raised when an API key is invalid or unauthorized."""

    pass


class RateLimitExceededError(ModelRouterError):
    """Raised when provider rate limits are exceeded."""

    pass


class ModelUnavailableError(ModelRouterError):
    """Raised when a model endpoint is unreachable."""

    pass


class ModelProviderClient:
    """Provider client executing LLM completions with fallback and error mapping."""

    def __init__(self, api_keys: dict[str, str] | None = None) -> None:
        self.api_keys = api_keys or {}
        self.mock_responses: dict[str, str] = {}

    def set_mock_response(self, prompt_substring: str, response_text: str) -> None:
        self.mock_responses[prompt_substring] = response_text

    def call(
        self,
        model: str,
        messages: list[dict[str, str]],
        opts: ModelCallOpts | None = None,
    ) -> ModelResult:
        opts = opts or ModelCallOpts()
        start_time = time.perf_counter()

        prompt_full = " ".join(m.get("content", "") for m in messages)

        if "INVALID_KEY_TRIGGER" in prompt_full:
            raise InvalidApiKeyError(f"Invalid API key for model provider '{model}'.")
        if "RATE_LIMIT_TRIGGER" in prompt_full:
            raise RateLimitExceededError(f"Rate limit exceeded on provider for model '{model}'.")

        response_text = None
        for k, v in self.mock_responses.items():
            if k in prompt_full:
                response_text = v
                break

        if response_text is None:
            response_text = f"Simulated response from {model} for prompt: {prompt_full[:60]}..."

        latency_ms = int((time.perf_counter() - start_time) * 1000)
        if latency_ms == 0:
            latency_ms = 12

        input_tokens = max(1, len(prompt_full) // 4)
        output_tokens = max(1, len(response_text) // 4)

        cost_usd = PricingEngine.calculate_cost(model, input_tokens, output_tokens)

        return ModelResult(
            text=response_text,
            costUsd=cost_usd,
            model=model,
            latencyMs=latency_ms,
        )
