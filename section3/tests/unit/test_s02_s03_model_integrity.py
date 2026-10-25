"""S-02 (team-scoped role models) and S-03 (opts.extra cannot override the model)."""

from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_router import api as api_module
from harness_router.api import get_principal, router
from harness_router.config import MODEL_PRICING_CATALOG
from harness_router.providers import ModelProviderClient

CALL = {"agentId": "a", "role": "primary", "messages": [{"role": "user", "content": "hi"}]}


def _client(team: str, handler=None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: SimpleNamespace(
        userId="u", teamId=team, role="engineer"
    )
    if handler is not None:
        api_module.get_router_service().client = ModelProviderClient(
            transport=httpx.MockTransport(handler)
        )
    return TestClient(app)


def _ok(seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        seen.append(body["model"])
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    return handler


def test_team_config_does_not_change_another_teams_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://provider.test")
    seen: list[str] = []
    a = _client("team-a", _ok(seen))
    b = _client("team-b")
    default = api_module.get_router_service().role_models["primary"]
    other = next(m for m in MODEL_PRICING_CATALOG if m != default)
    svc = api_module.get_router_service()
    svc.team_role_models.clear()

    cfg = a.post("/api/v1/models/config", json={"role": "primary", "model": other})
    assert cfg.status_code == 200
    assert b.post("/api/v1/models/call", json={**CALL, "apiKey": "k"}).status_code == 200
    assert a.post("/api/v1/models/call", json={**CALL, "apiKey": "k"}).status_code == 200
    assert seen[0].endswith(default.split("/", 1)[-1])  # team-b still on the global default
    assert seen[1].endswith(other.split("/", 1)[-1])  # team-a got its own override
    assert svc.role_models["primary"] == default  # global default untouched
    svc.team_role_models.clear()


def test_opts_extra_cannot_override_the_called_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://provider.test")
    seen: list[str] = []
    c = _client("team-c", _ok(seen))
    body = {**CALL, "apiKey": "k", "opts": {"extra": {"model": "evil/expensive", "top_p": 0.5}}}
    r = c.post("/api/v1/models/call", json=body)
    assert r.status_code == 200
    assert "evil" not in seen[0]
    assert r.json()["model"].split("/", 1)[-1] == seen[0]
