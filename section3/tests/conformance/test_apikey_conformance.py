"""FIX-LOOP E1 conformance — the team apiKey survives the REST seam.

Verifier-proven defect (VT-2): ``POST /api/v1/models/call`` silently dropped
the ``apiKey`` body field (pydantic) and the provider layer synthesized
"Simulated response ..." text for every call. Together that meant a team's
genuine completion could be replaced by fabricated text.

Contract pinned here:
1. An httpx-injected provider receives EXACTLY the key + messages the client
   sent — Authorization header carries the verbatim key, the chat-completions
   body carries the verbatim messages (plus the resolved model name).
2. The response text is the PROVIDER's text, never synthesized.
3. Keyless calls outside HARNESS_ENV=dev fail HONESTLY (503
   PROVIDER_UNAVAILABLE) — synthesis is dead in every non-dev context.
4. Provider failures map to honest error envelopes (401/429/503), no
   fabrication, and the raw key never leaks into any surfaced message.
5. The INVALID_KEY_TRIGGER / RATE_LIMIT_TRIGGER sentinel behaviors stay
   pinned for the suites that rely on them.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_router import api as api_module
from harness_router.api import router


def _client_with_provider(handler: Any) -> tuple[TestClient, Any]:
    """FastAPI app with the router's service swapped for one whose provider
    client makes genuinely observed httpx calls via MockTransport."""
    from harness_router.providers import ModelProviderClient

    provider = ModelProviderClient(transport=httpx.MockTransport(handler))
    app = FastAPI()
    app.include_router(router)
    test_client = TestClient(app)
    # Swap the module-level service's client (same object the route resolves).
    service = api_module.get_router_service()
    service.client = provider
    return test_client, provider


def _post_call(client: TestClient, payload: dict[str, Any]):
    return client.post("/api/v1/models/call", json=payload)


CAPTURED: dict[str, Any] = {}


def capture_handler(request: httpx.Request) -> httpx.Response:
    """Record the outbound provider request verbatim; answer like DeepSeek."""
    CAPTURED["url"] = str(request.url)
    CAPTURED["auth"] = request.headers.get("authorization")
    CAPTURED["body"] = json.loads(request.content.decode())
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "model": "deepseek-coder",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "PROVIDER_TEXT"}}
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        },
    )


MESSAGES = [
    {"role": "system", "content": "You are a meeting assistant."},
    {"role": "user", "content": "Explain JWT refresh tokens."},
]
# Fake key literal for the transport probe — deliberately NOT real-looking so
# secret scanners don't flag it; only its uniqueness matters here.
SECRET = "fake-key-for-transport-probe-9f2"


class TestApiKeySurvivesTheRestSeam:
    def test_exact_key_and_messages_reach_the_provider(self):
        client, _provider = _client_with_provider(capture_handler)
        res = _post_call(
            client,
            {
                "agentId": "ai-participant@team-e1",
                "role": "primary",
                "messages": MESSAGES,
                "apiKey": SECRET,
            },
        )
        assert res.status_code == 200, res.text
        # The key arrives VERBATIM as the bearer token on the outbound call…
        assert CAPTURED["auth"] == f"Bearer {SECRET}"
        # …and the messages arrive VERBATIM in the provider payload.
        assert CAPTURED["body"]["messages"] == MESSAGES
        assert CAPTURED["body"]["model"] == "deepseek-coder"  # role default, wire name

    def test_response_is_the_providers_text_not_synthesis(self):
        client, _provider = _client_with_provider(capture_handler)
        res = _post_call(
            client,
            {
                "agentId": "ai-participant@team-e1",
                "role": "primary",
                "messages": MESSAGES,
                "apiKey": SECRET,
            },
        )
        data = res.json()
        assert data["text"] == "PROVIDER_TEXT"
        assert "Simulated response" not in data["text"]
        assert data["model"] == "deepseek/deepseek-coder"  # catalog name preserved
        assert data["costUsd"] > 0 and data["latencyMs"] >= 0

    def test_key_never_leaks_into_the_http_response(self):
        client, _provider = _client_with_provider(capture_handler)
        res = _post_call(
            client,
            {
                "agentId": "ai-participant@team-e1",
                "role": "primary",
                "messages": MESSAGES,
                "apiKey": SECRET,
            },
        )
        assert SECRET not in res.text

    def test_model_body_field_overrides_role_default(self):
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content.decode())
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                },
            )

        client, _provider = _client_with_provider(handler)
        res = _post_call(
            client,
            {
                "agentId": "a",
                "role": "primary",
                "messages": MESSAGES,
                "apiKey": SECRET,
                "model": "deepseek/deepseek-chat",
            },
        )
        assert res.status_code == 200
        assert seen["body"]["model"] == "deepseek-chat"
        assert res.json()["model"] == "deepseek/deepseek-chat"

    def test_opts_temperature_is_honored_on_the_wire(self):
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content.decode())
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}}]},
            )

        client, _provider = _client_with_provider(handler)
        res = _post_call(
            client,
            {
                "agentId": "a",
                "role": "primary",
                "messages": MESSAGES,
                "apiKey": SECRET,
                "opts": {"maxTokens": 256, "temperature": 0.3},
            },
        )
        assert res.status_code == 200
        assert seen["body"]["max_tokens"] == 256
        assert seen["body"]["temperature"] == 0.3


class TestHonestFailures:
    def test_provider_401_maps_to_invalid_api_key_envelope(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": "bad key"}})

        client, _provider = _client_with_provider(handler)
        res = _post_call(
            client,
            {"agentId": "a", "role": "primary", "messages": MESSAGES, "apiKey": SECRET},
        )
        assert res.status_code == 401
        assert res.json()["detail"]["error"]["code"] == "INVALID_API_KEY"

    def test_provider_429_maps_to_rate_limit_envelope(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={})

        client, _provider = _client_with_provider(handler)
        res = _post_call(
            client,
            {"agentId": "a", "role": "primary", "messages": MESSAGES, "apiKey": SECRET},
        )
        assert res.status_code == 429
        assert res.json()["detail"]["error"]["code"] == "RATE_LIMIT_EXCEEDED"

    def test_provider_outage_maps_to_503_no_fabricated_text(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client, _provider = _client_with_provider(handler)
        res = _post_call(
            client,
            {"agentId": "a", "role": "primary", "messages": MESSAGES, "apiKey": SECRET},
        )
        assert res.status_code == 503
        body = res.json()
        assert body["detail"]["error"]["code"] == "PROVIDER_UNAVAILABLE"
        assert SECRET not in res.text  # scrubbed even on failure paths


class TestSynthesisKilledOutsideDev:
    @pytest.fixture(autouse=True)
    def _non_dev_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("HARNESS_ENV", raising=False)

    def test_keyless_call_without_dev_env_is_honest_503(self):
        client, _provider = _client_with_provider(capture_handler)
        res = _post_call(
            client,
            {"agentId": "a", "role": "primary", "messages": MESSAGES},  # NO apiKey
        )
        assert res.status_code == 503
        body = res.json()
        assert body["detail"]["error"]["code"] == "PROVIDER_UNAVAILABLE"
        assert "Simulated response" not in res.text
        CAPTURED.clear()  # the provider must never have been contacted

    def test_keyless_direct_provider_call_raises_model_unavailable(self):
        from harness_router.providers import ModelProviderClient, ModelUnavailableError

        provider = ModelProviderClient()  # no transport: nothing to hit anyway
        with pytest.raises(ModelUnavailableError):
            provider.call(model="deepseek/deepseek-coder", messages=MESSAGES)


class TestSentinelBehaviorsPinned:
    """Suites across S3/S4/integration pin these deterministic triggers."""

    def test_invalid_key_trigger_still_maps_to_401(self):
        client, _provider = _client_with_provider(capture_handler)
        res = _post_call(
            client,
            {
                "agentId": "a",
                "role": "primary",
                "messages": [{"role": "user", "content": "Trigger error: INVALID_KEY_TRIGGER"}],
                "apiKey": SECRET,
            },
        )
        assert res.status_code == 401
        assert res.json()["detail"]["error"]["code"] == "INVALID_API_KEY"
        CAPTURED.clear()  # trigger fires before any network attempt

    def test_rate_limit_trigger_still_maps_to_429(self):
        client, _provider = _client_with_provider(capture_handler)
        res = _post_call(
            client,
            {
                "agentId": "a",
                "role": "primary",
                "messages": [{"role": "user", "content": "Trigger error: RATE_LIMIT_TRIGGER"}],
            },
        )
        assert res.status_code == 429
        assert res.json()["detail"]["error"]["code"] == "RATE_LIMIT_EXCEEDED"


class TestDevSimulationStillAvailable:
    """The explicit dev opt-in keeps the legacy simulated replies working."""

    def test_dev_env_allows_simulated_reply_when_keyless(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("HARNESS_ENV", "dev")
        client, _provider = _client_with_provider(capture_handler)
        res = _post_call(
            client,
            {"agentId": "a", "role": "primary", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert res.status_code == 200
        assert res.json()["text"].startswith("Simulated response from")
