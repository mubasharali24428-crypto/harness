"""API contract tests: gated access, index lifecycle, frozen search payload."""

import asyncio

from conftest import AUTH_HEADERS
from sqlalchemy import text

from harness_context.embeddings import EmbeddingBackend

PROJECT = "p-api"


class _StubBackend(EmbeddingBackend):
    """Records embed() calls so tests can prove what reached the model layer."""

    def __init__(self) -> None:
        self.dims = 8
        self.calls = 0

    async def embed(self, texts):
        self.calls += len(texts)
        return [[float(len(t) % 7), 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] for t in texts]


async def test_routes_require_bearer(client, mini_repo):
    res = await client.post(
        "/api/v1/context/index",
        json={"repoPath": str(mini_repo), "projectId": PROJECT},
    )
    assert res.status_code == 401
    res = await client.post(
        "/api/v1/context/search", json={"query": "x", "projectId": PROJECT}
    )
    assert res.status_code == 401
    res = await client.get("/api/v1/context/index/some-id")
    assert res.status_code == 401
    # forged token != seeded sha256 -> rejected by contracts.make_bearer_dep
    res = await client.post(
        "/api/v1/context/search",
        json={"query": "x", "projectId": PROJECT},
        headers={"Authorization": "Bearer forged-token"},
    )
    assert res.status_code == 401


async def test_healthz_requires_auth_too(client):
    res = await client.get("/api/v1/healthz")
    assert res.status_code == 401
    res = await client.get("/api/v1/healthz", headers=AUTH_HEADERS)
    assert res.status_code == 200 and res.json() == {"ok": True}


async def test_root_health_is_liveness_only(client):
    """Compose healthchecks hit /health unauthenticated (repo convention)."""
    res = await client.get("/health")
    assert res.status_code == 200 and res.json() == {"ok": True}


async def test_index_lifecycle_over_http(client, db, mini_repo):
    res = await client.post(
        "/api/v1/context/index",
        json={"repoPath": str(mini_repo), "projectId": PROJECT},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 200
    body = res.json()
    job_id = body["jobId"]
    assert body["status"] in {"pending", "indexing", "completed"}

    status_body = None
    for _ in range(100):
        res = await client.get(f"/api/v1/context/index/{job_id}", headers=AUTH_HEADERS)
        assert res.status_code == 200
        status_body = res.json()
        if status_body["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.01)
    assert status_body is not None and status_body["status"] == "completed"
    assert status_body["filesIndexed"] == 5
    assert status_body["chunksIndexed"] > 0
    assert status_body["error"] is None

    async with db() as s:
        n = (
            await s.execute(
                text("SELECT COUNT(*) FROM context_chunks WHERE project_id = :p"),
                {"p": PROJECT},
            )
        ).scalar_one()
    assert int(n) == status_body["chunksIndexed"]

    res = await client.get("/api/v1/context/index/does-not-exist", headers=AUTH_HEADERS)
    assert res.status_code == 404


async def test_index_rejects_bad_repo_path(client):
    res = await client.post(
        "/api/v1/context/index",
        json={"repoPath": "/definitely/not/a/repo", "projectId": PROJECT},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 422


async def test_search_wire_contract_camelcase(client, db, mini_repo):
    app = client.app
    svc = app.state.context_service
    from harness_context.service import IndexJob

    await svc.reindex(
        IndexJob(id="j-wire", project_id=PROJECT, repo_path=str(mini_repo)), mini_repo
    )

    res = await client.post(
        "/api/v1/context/search",
        json={"query": "emit metrics udp", "projectId": PROJECT, "topK": 2},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 200
    data = res.json()
    assert set(data) == {"projectId", "query", "results"}
    assert data["projectId"] == PROJECT
    assert len(data["results"]) <= 2
    for hit in data["results"]:
        assert set(hit) == {"path", "lang", "startLine", "endLine", "content", "score"}


async def test_unwired_app_fails_closed():
    """Router mounted with NO app.state wiring -> every route 401s (fail closed),
    never anonymous service."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from harness_context.api import router

    plain = FastAPI()
    plain.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=plain), base_url="http://t") as c:
        res = await c.post(
            "/api/v1/context/index", json={"repoPath": "/tmp/x", "projectId": "p"}
        )
        assert res.status_code == 401
        res = await c.post("/api/v1/context/search", json={"query": "q", "projectId": "p"})
        assert res.status_code == 401
