import pytest
from harness_contracts.signatures import ModelCallOpts

from harness_router.providers import (
    InvalidApiKeyError,
    ModelProviderClient,
    RateLimitExceededError,
)


def test_provider_client_successful_call():
    client = ModelProviderClient()
    client.set_mock_response("fibonacci", "def fib(n): return n if n <= 1 else fib(n-1) + fib(n-2)")

    result = client.call(
        model="deepseek/deepseek-coder",
        messages=[{"role": "user", "content": "Write fibonacci function"}],
        opts=ModelCallOpts(maxTokens=1024, temperature=0.2),
    )

    assert "def fib(n):" in result.text
    assert result.model == "deepseek/deepseek-coder"
    assert result.costUsd >= 0.0
    assert result.latencyMs > 0


def test_provider_client_invalid_api_key():
    client = ModelProviderClient()
    with pytest.raises(InvalidApiKeyError):
        client.call(
            model="openai/gpt-4o",
            messages=[{"role": "user", "content": "Trigger error: INVALID_KEY_TRIGGER"}],
        )


def test_provider_client_rate_limit():
    client = ModelProviderClient()
    with pytest.raises(RateLimitExceededError):
        client.call(
            model="anthropic/claude-3-5-sonnet",
            messages=[{"role": "user", "content": "Trigger error: RATE_LIMIT_TRIGGER"}],
        )
