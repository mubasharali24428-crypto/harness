"""The done-signal reviewer has no team key of its own, so reviewer calls fall back to the
deployment's provider key. Every other role still needs a key; a caller's own key always wins."""

import json
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_router import api as api_module
from harness_router.api import get_principal, router
from harness_router.providers import ModelProviderClient


def _client(seen_auth: list) -> TestClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("authorization"))
        json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: SimpleNamespace(
        userId="u", teamId="team-r", role="engineer"
    )
    api_module.get_router_service().client = ModelProviderClient(
        transport=httpx.MockTransport(handler)
    )
    return TestClient(app)


def _call(role: str, **extra):
    return {
        "agentId": "reviewer",
        "role": role,
        "messages": [{"role": "user", "content": "hi"}],
        **extra,
    }


def test_reviewer_call_without_a_team_key_uses_the_operator_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://provider.test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "operator-key")
    seen: list[str] = []
    resp = _client(seen).post("/api/v1/models/call", json=_call("reviewer"))
    assert resp.status_code == 200
    assert seen == ["Bearer operator-key"]


def test_primary_call_without_a_team_key_is_still_refused(monkeypatch):
    monkeypatch.setenv("HARNESS_ENV", "prod")  # the simulated path is dev-only
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://provider.test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "operator-key")
    seen: list[str] = []
    resp = _client(seen).post("/api/v1/models/call", json=_call("primary"))
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"]["code"] == "PROVIDER_UNAVAILABLE"
    assert seen == []


def test_callers_own_key_wins_over_the_operator_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://provider.test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "operator-key")
    seen: list[str] = []
    resp = _client(seen).post("/api/v1/models/call", json=_call("reviewer", apiKey="team-key"))
    assert resp.status_code == 200
    assert seen == ["Bearer team-key"]
