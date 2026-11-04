"""Provider registry: endpoint resolution and per-protocol wire adapters.

The registry exists because the router used to send every model to one URL.
These tests pin the two things that made that wrong — which URL a model id
resolves to, and whether the body/headers/parse match the protocol of the
provider that actually serves it.
"""

import json
from typing import Any

import httpx
import pytest
from harness_contracts.signatures import ModelCallOpts

from harness_router import provider_registry as reg
from harness_router.providers import ModelProviderClient, priced_cost


@pytest.fixture(autouse=True)
def _clean_endpoint_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The adapter tests assert the *registry* URL, so a runner that happens to
    export a deployment override must not change the answer."""
    for var in (
        reg.GLOBAL_BASE_URL_ENV,
        reg.LEGACY_BASE_URL_ENV,
        "OPENAI_BASE_URL",
        "ANTHROPIC_BASE_URL",
        "GROQ_BASE_URL",
        "OLLAMA_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)


def _capturing(body: dict[str, Any]) -> tuple[ModelProviderClient, dict[str, Any]]:
    """A client whose transport records the request and replays `body`."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["json"] = json.loads(request.content.decode())
        return httpx.Response(200, json=body)

    return ModelProviderClient(transport=httpx.MockTransport(handler)), seen


MESSAGES = [{"role": "user", "content": "hi"}]


class TestResolveEndpoint:
    def test_registry_default_uses_the_provider_native_protocol(self) -> None:
        endpoint = reg.resolve_endpoint("anthropic/claude-sonnet-4", environ={})
        assert endpoint.protocol == "anthropic"
        assert endpoint.url == "https://api.anthropic.com/v1/messages"
        assert endpoint.source == "registry"

        gemini = reg.resolve_endpoint("google/gemini-2.5-flash", environ={})
        assert gemini.protocol == "gemini"
        assert gemini.url == "https://generativelanguage.googleapis.com"

        groq = reg.resolve_endpoint("groq/llama-3.3-70b-versatile", environ={})
        assert groq.url == "https://api.groq.com/openai/v1/chat/completions"

    def test_provider_env_override_wins_and_keeps_the_native_protocol(self) -> None:
        endpoint = reg.resolve_endpoint(
            "anthropic/claude-sonnet-4",
            environ={"ANTHROPIC_BASE_URL": "https://proxy.internal/anthropic"},
        )
        assert endpoint.protocol == "anthropic"
        assert endpoint.url == "https://proxy.internal/anthropic/v1/messages"
        assert endpoint.source == "provider-env"

    def test_gateway_override_forces_openai_protocol(self) -> None:
        """One URL serving many vendors speaks the OpenAI shape, whatever the
        model id's nominal provider is."""
        endpoint = reg.resolve_endpoint(
            "anthropic/claude-sonnet-4",
            environ={"HARNESS_PROVIDER_BASE_URL": "https://openrouter.ai/api/v1"},
        )
        assert endpoint.protocol == "openai"
        assert endpoint.url == "https://openrouter.ai/api/v1/chat/completions"
        assert endpoint.source == "global-env"

    def test_legacy_single_endpoint_deployment_still_routes_everything(self) -> None:
        """DEEPSEEK_BASE_URL is what existing installs and suites set. It must
        keep winning over the registry default, as a gateway."""
        endpoint = reg.resolve_endpoint(
            "openai/gpt-4o", environ={"DEEPSEEK_BASE_URL": "http://provider.test"}
        )
        assert endpoint.url == "http://provider.test/chat/completions"
        assert endpoint.protocol == "openai"
        assert endpoint.source == "legacy-env"

    def test_unregistered_model_falls_back_instead_of_failing(self) -> None:
        """The registry is a convenience, not a permission."""
        endpoint = reg.resolve_endpoint(
            "some-lab-we-have-not-listed/yet",
            environ={"DEEPSEEK_BASE_URL": "http://provider.test"},
        )
        assert endpoint.url == "http://provider.test/chat/completions"
        assert endpoint.provider == "deployment-default"

    def test_provider_of_unknown_prefix_is_none(self) -> None:
        assert reg.provider_of("groq/x").name == "groq"
        assert reg.provider_of("no-prefix") is None
        assert reg.wire_name("groq/llama-3.3-70b") == "llama-3.3-70b"


class TestAnthropicAdapter:
    def test_request_shape_and_response_parse(self) -> None:
        client, seen = _capturing(
            {
                "content": [{"type": "text", "text": "hello from claude"}],
                "usage": {"input_tokens": 11, "output_tokens": 7},
            }
        )
        result = client.call(
            "anthropic/claude-sonnet-4",
            [{"role": "system", "content": "be brief"}, *MESSAGES],
            ModelCallOpts(maxTokens=256, temperature=0.2),
            api_key="sk-ant-test",
        )
        assert result.text == "hello from claude"
        assert seen["url"] == "https://api.anthropic.com/v1/messages"
        assert seen["headers"]["x-api-key"] == "sk-ant-test"
        assert seen["headers"]["anthropic-version"] == reg.ANTHROPIC_VERSION
        assert "authorization" not in seen["headers"]
        body = seen["json"]
        assert body["model"] == "claude-sonnet-4"
        assert body["system"] == "be brief"
        assert body["messages"] == MESSAGES  # system turns are lifted out
        assert body["max_tokens"] == 256  # required by the API
        assert body["temperature"] == 0.2

    def test_max_tokens_gets_a_floor_so_the_call_does_not_400(self) -> None:
        client, seen = _capturing(
            {"content": [{"type": "text", "text": "ok"}], "usage": {}}
        )
        client.call("anthropic/claude-sonnet-4", MESSAGES, ModelCallOpts(), api_key="k")
        assert seen["json"]["max_tokens"] == 4096

    def test_key_never_surfaces_in_the_result(self) -> None:
        client, _ = _capturing(
            {"content": [{"type": "text", "text": "s3cr3t-echo"}], "usage": {}}
        )
        result = client.call(
            "anthropic/claude-sonnet-4", MESSAGES, ModelCallOpts(), api_key="sk-ant-s3cr3t"
        )
        assert "sk-ant-s3cr3t" not in repr(result)


class TestGeminiAdapter:
    def test_model_lives_in_the_path_and_roles_are_mapped(self) -> None:
        client, seen = _capturing(
            {
                "candidates": [{"content": {"parts": [{"text": "gemini says hi"}]}}],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
            }
        )
        result = client.call(
            "google/gemini-2.5-flash",
            [
                {"role": "system", "content": "tone"},
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "yo"},
                {"role": "user", "content": "again"},
            ],
            ModelCallOpts(maxTokens=128),
            api_key="AIza-test",
        )
        assert result.text == "gemini says hi"
        assert seen["url"].endswith("/v1beta/models/gemini-2.5-flash:generateContent")
        assert seen["headers"]["x-goog-api-key"] == "AIza-test"
        body = seen["json"]
        assert body["systemInstruction"]["parts"][0]["text"] == "tone"
        assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
        assert body["generationConfig"] == {"maxOutputTokens": 128}
        assert "model" not in body  # addressed by URL, not body


class TestOpenAICompatibleAdapter:
    def test_unchanged_shape_for_a_gateway_model(self) -> None:
        client, seen = _capturing(
            {
                "choices": [{"message": {"content": "groked"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            }
        )
        result = client.call(
            "groq/llama-3.3-70b-versatile",
            MESSAGES,
            ModelCallOpts(maxTokens=64),
            api_key="gsk-test",
        )
        assert result.text == "groked"
        assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
        assert seen["headers"]["authorization"] == "Bearer gsk-test"
        assert seen["json"]["model"] == "llama-3.3-70b-versatile"
        assert seen["json"]["max_tokens"] == 64
        assert result.latencyMs >= 1


class TestPricingHonesty:
    def test_priced_model_bills_its_real_rate(self) -> None:
        assert priced_cost("deepseek/deepseek-chat", 1_000_000, 1_000_000) > 0

    def test_unpriced_model_bills_zero_rather_than_a_guess(self) -> None:
        """PricingEngine's fallback is $0.50/$1.50 for anything it does not
        know. With a registry this size that would invent spend for free local
        runs and under-bill frontier models, so an unknown id costs zero and
        the gap is logged."""
        from decimal import Decimal

        assert priced_cost("ollama/qwen3:14b", 500_000, 500_000) == Decimal("0")
        assert priced_cost("brandnew-lab/model-v9", 500_000, 500_000) == Decimal("0")

    def test_unpriced_call_records_zero_cost_on_the_result(self) -> None:
        client, _ = _capturing(
            {
                "choices": [{"message": {"content": "x"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        )
        result = client.call("ollama/qwen3:14b", MESSAGES, ModelCallOpts(), api_key="unused")
        assert result.costUsd == 0.0


def test_registry_size_is_at_least_fifty():
    assert len(reg.providers()) >= 50


@pytest.mark.parametrize("name", ["openai", "anthropic", "google", "groq", "ollama"])
def test_key_providers_resolve(name: str) -> None:
    provider = reg.get(name)
    assert provider is not None
    assert provider.base_url
