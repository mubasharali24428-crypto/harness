"""A1-S3 — team model policy at the Section 3 call seam (offline, fake provider client)."""

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.signatures import ModelResult
from harness_security_policy.model_policy import TeamModelPolicy

from harness_router import api, model_policy_seam
from harness_router.providers import ModelUnavailableError, RateLimitExceededError

A, B, C = "deepseek/deepseek-chat", "openai/gpt-4o-mini", "qwen/qwen-2.5-72b"


class FakeClient:
    def __init__(self):
        self.calls: list[str] = []
        self.down: dict[str, Exception] = {}

    def call(self, model, messages, opts=None, api_key=None):
        self.calls.append(model)
        if model in self.down:
            raise self.down[model]
        text = " ".join(m["content"] for m in messages)
        return ModelResult(text=f"{model}:{text}", model=model, costUsd=0.001, latencyMs=7)


@pytest.fixture
def env(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(api._service, "client", fake)
    state = {"team": "t1", "role": "engineer"}
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_principal] = lambda: SimpleNamespace(
        userId="u", teamId=state["team"], role=state["role"]
    )
    policies: dict[str, TeamModelPolicy] = {}

    async def provider(team_id):
        return policies.get(team_id, TeamModelPolicy())

    model_policy_seam.set_policy_provider(provider)
    model_policy_seam.TENANT_CACHE.invalidate_team("t1")
    model_policy_seam.TENANT_CACHE.invalidate_team("t2")
    yield TestClient(app), fake, policies, state, app
    model_policy_seam.set_policy_provider(None)


def call(client, **kw):
    body = {
        "agentId": "a",
        "role": "primary",
        "messages": [{"role": "user", "content": "hi"}],
        **kw,
    }
    return client.post("/api/v1/models/call", json=body)


def test_requested_model_preserved_and_unapproved_denied(env):
    client, fake, policies, *_ = env
    policies["t1"] = TeamModelPolicy(allowlist_enforced=True, approved_models=frozenset({A}))
    r = call(client, model=A)
    assert r.status_code == 200 and r.json()["model"] == A
    r = call(client, model=B)
    assert r.status_code == 403 and r.json()["detail"]["error"]["code"] == "MODEL_NOT_ALLOWED"
    assert fake.calls == [A]  # denied request never reached a provider, never substituted


def test_unknown_requested_model_is_rejected_not_remapped(env):
    client, fake, *_ = env
    r = call(client, model="made/up")
    assert r.status_code == 422 and r.json()["detail"]["error"]["code"] == "MODEL_UNKNOWN"
    assert fake.calls == []


def test_default_role_model_must_also_be_approved(env):
    client, fake, policies, *_ = env
    policies["t1"] = TeamModelPolicy(allowlist_enforced=True, approved_models=frozenset({B}))
    assert call(client).status_code == 403  # default primary not approved -> no silent default


def test_routing_rule_used_only_without_explicit_model(env):
    client, fake, policies, *_ = env
    policies["t1"] = TeamModelPolicy(routing_rules=(("review", B),))
    r = call(client, taskType="review")
    assert r.json()["model"] == B and r.headers["X-Harness-Model-Source"] == "routing_rule"
    r = call(client, taskType="review", model=A)
    assert r.json()["model"] == A and r.headers["X-Harness-Model-Source"] == "requested"


def test_outage_without_optin_is_honest_503_with_optin_attributed_fallback(env):
    client, fake, policies, *_ = env
    fake.down[A] = ModelUnavailableError("down")
    r = call(client, model=A)
    assert r.status_code == 503 and fake.calls == [A]
    policies["t1"] = TeamModelPolicy(
        allowlist_enforced=True,
        approved_models=frozenset({A, B}),
        fallback_enabled=True,
        fallback_equivalents=((A, (C, B)),),  # C is unapproved -> skipped
    )
    fake.calls.clear()
    r = call(client, model=A)
    assert r.status_code == 200 and r.json()["model"] == B
    assert r.headers["X-Harness-Fallback-From"] == A and r.headers["X-Harness-Served-Model"] == B
    assert r.headers["X-Harness-Fallback-Reason"] == "unavailable" and fake.calls == [A, B]


def test_invalid_key_never_falls_back(env):
    from harness_router.providers import InvalidApiKeyError

    client, fake, policies, *_ = env
    policies["t1"] = TeamModelPolicy(fallback_enabled=True, fallback_equivalents=((A, (B,)),))
    fake.down[A] = InvalidApiKeyError("bad")
    assert call(client, model=A).status_code == 401 and fake.calls == [A]
    fake.calls.clear()
    fake.down[A] = RateLimitExceededError("slow")
    assert call(client, model=A).json()["model"] == B  # rate limit is a legit fallback trigger


def test_cache_hit_isolated_per_tenant_ttl_and_invalidation(env):
    client, fake, policies, state, _ = env
    policies["t1"] = TeamModelPolicy(cache_enabled=True, cache_ttl_seconds=60)
    policies["t2"] = TeamModelPolicy(cache_enabled=True, cache_ttl_seconds=60)
    opts = {"temperature": 0}
    assert call(client, model=A, opts=opts).headers["X-Harness-Cache"] == "miss"
    assert call(client, model=A, opts=opts).headers["X-Harness-Cache"] == "hit"
    assert fake.calls == [A]  # second answered from cache
    state["team"] = "t2"
    assert (
        call(client, model=A, opts=opts).headers["X-Harness-Cache"] == "miss"
    )  # no cross-tenant hit
    state["team"] = "t1"
    assert call(client, model=A, opts={"temperature": 0.7}).headers.get("X-Harness-Cache") is None
    assert client.post("/api/v1/models/cache/invalidate").json() == {"invalidated": 1}
    assert call(client, model=A, opts=opts).headers["X-Harness-Cache"] == "miss"
    state["team"] = "t2"  # t1's invalidation did not touch t2
    assert call(client, model=A, opts=opts).headers["X-Harness-Cache"] == "hit"


def test_cache_off_by_default(env):
    client, fake, *_ = env
    call(client, model=A, opts={"temperature": 0})
    call(client, model=A, opts={"temperature": 0})
    assert fake.calls == [A, A]


def test_compare_ranks_scored_and_enforces_allowlist(env):
    client, fake, policies, *_ = env

    def fake_call(model, messages, opts=None, api_key=None):
        fake.calls.append(model)
        return ModelResult(
            text="answer 42" if model == A else "nope",
            model=model,
            costUsd=0.01 if model == A else 0.001,
            latencyMs=5,
        )

    fake.call = fake_call
    body = {
        "messages": [{"role": "user", "content": "q"}],
        "models": [A, B],
        "expectedContains": ["42"],
    }
    out = client.post("/api/v1/models/compare", json=body).json()
    ranks = {r["model"]: r["rank"] for r in out["results"]}
    assert ranks == {A: 1, B: 2} and out["simulated"] is True
    body.pop("expectedContains")
    assert all(
        r["rank"] is None
        for r in client.post("/api/v1/models/compare", json=body).json()["results"]
    )
    policies["t1"] = TeamModelPolicy(allowlist_enforced=True, approved_models=frozenset({A}))
    fake.calls.clear()
    assert client.post("/api/v1/models/compare", json=body).status_code == 403
    assert fake.calls == []  # nothing ran, nothing was billed


def test_local_discovery_ssrf_and_probe(env, monkeypatch):
    client, _, _, _, app = env
    monkeypatch.setattr(
        api,
        "_resolve",
        lambda h: {
            "ollama.local": ["127.0.0.1"],
            "meta": ["169.254.169.254"],
            "gpu.lan": ["10.0.0.5"],
        }.get(h, ["8.8.8.8"]),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/models":
            return httpx.Response(404)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen:7b"}]})
        return httpx.Response(404)

    app.state.local_model_transport = httpx.MockTransport(handler)
    ok = client.get(
        "/api/v1/models/local/discover", params={"baseUrl": "http://ollama.local:11434"}
    )
    assert ok.status_code == 200 and ok.json()["healthy"] and ok.json()["models"] == ["qwen:7b"]
    for url, reason in (
        ("http://meta/", "local.link_local_denied"),
        ("http://gpu.lan/", "local.private_denied"),
        ("http://evil.com/", "local.public_denied"),
    ):
        r = client.get("/api/v1/models/local/discover", params={"baseUrl": url})
        assert r.status_code == 422 and r.json()["detail"]["error"]["message"] == reason
    monkeypatch.setenv("HARNESS_LOCAL_MODEL_HOSTS", "gpu.lan")
    assert (
        client.get(
            "/api/v1/models/local/discover", params={"baseUrl": "http://gpu.lan/"}
        ).status_code
        == 200
    )
    app.state.local_model_transport = httpx.MockTransport(lambda r: httpx.Response(500))
    down = client.get(
        "/api/v1/models/local/discover", params={"baseUrl": "http://ollama.local"}
    ).json()
    assert down["healthy"] is False
    app.state.local_model_transport = httpx.MockTransport(
        lambda r: httpx.Response(200, content=b"x" * 2_000_000)
    )
    big = client.get(
        "/api/v1/models/local/discover", params={"baseUrl": "http://ollama.local"}
    ).json()
    assert big["healthy"] is False


async def test_pool_provider_reads_rows_caches_and_fails_closed():
    class Pool:
        def __init__(self):
            self.n = 0
            self.fail = False

        async def fetch(self, sql, team):
            self.n += 1
            if self.fail:
                raise RuntimeError("db down")
            if "model_allowlist_entries" in sql:
                return [{"model": A}]
            return [{"value": json.dumps({"allowlistEnforced": True})}]

    now = [0.0]
    pool = Pool()
    prov = model_policy_seam.pool_policy_provider(pool, ttl=10, clock=lambda: now[0])
    p = await prov("t1")
    assert p.allowlist_enforced and p.approved_models == frozenset({A})
    await prov("t1")
    assert pool.n == 2  # second read served from policy cache
    now[0] = 11
    pool.fail = True
    closed = await model_policy_seam.pool_policy_provider(pool)("t9")
    assert closed.allowlist_enforced and not closed.approved_models
