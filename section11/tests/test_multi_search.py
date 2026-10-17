"""F013 proofs: one query across up to 10 projects, embedded ONCE.

Multi-search fans a single embedding over several indexes. These tests pin:
the query embeds exactly once (no N+1 model calls), results are per-project
and scored on the same comparable scale, duplicate ids collapse, and EVERY
listed project must pass ownership — one foreign id denies the whole call
(no partial information leak about which ids were valid).
"""


from conftest import AUTH_HEADERS, seed_project
from sqlalchemy import text

from harness_context.embeddings import EmbeddingBackend, HashingEmbedding

PROJECT_A = "p-alpha"
PROJECT_B = "p-beta"


class _CountingBackend(EmbeddingBackend):
    def __init__(self, dims: int = 256) -> None:
        self._inner = HashingEmbedding(dims=dims)
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        return await self._inner.embed(texts)


async def _seed_chunk(db, project: str, path: str, content: str) -> None:
    backend = HashingEmbedding(dims=256)
    [vec] = await backend.embed([content])
    literal = "[" + ",".join(repr(float(v)) for v in vec) + "]"
    async with db() as s:
        await s.execute(
            text(
                "INSERT INTO context_chunks (project_id, path, lang, start_line,"
                " end_line, content, embedding, chunk_hash)"
                " VALUES (:p, :path, 'python', 1, 10, :c, :e, 'h')"
            ),
            {"p": project, "path": path, "c": content, "e": literal},
        )
        await s.commit()


async def test_one_embedding_for_all_projects(client, db):
    await seed_project(db, PROJECT_A)
    await seed_project(db, PROJECT_B)
    await _seed_chunk(db, PROJECT_A, "a.py", "session token rotation helpers")
    await _seed_chunk(db, PROJECT_B, "b.py", "unrelated css palette code")

    app = client.app
    counting = _CountingBackend(256)
    app.state.context_service.backend = counting

    res = await client.post(
        "/api/v1/context/search-multi",
        json={
            "query": "session token rotation",
            "projectIds": [PROJECT_A, PROJECT_B],
            "topK": 5,
        },
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 200, res.text
    assert counting.calls == 1, "query must embed once, not per project"

    body = res.json()
    assert [r["projectId"] for r in body["results"]] == [PROJECT_A, PROJECT_B]
    a_hits = body["results"][0]["results"]
    assert a_hits and a_hits[0]["path"] == "a.py"
    assert 0.0 <= a_hits[0]["score"] <= 1.0


async def test_duplicate_project_ids_collapse(client, db):
    await seed_project(db, PROJECT_A)
    await _seed_chunk(db, PROJECT_A, "a.py", "alpha content")
    res = await client.post(
        "/api/v1/context/search-multi",
        json={"query": "alpha", "projectIds": [PROJECT_A, PROJECT_A]},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 200
    assert [r["projectId"] for r in res.json()["results"]] == [PROJECT_A]


async def test_one_foreign_id_denies_whole_call(client, db):
    await seed_project(db, PROJECT_A)
    await seed_project(db, "p-foreign", team_id="team-other")
    res = await client.post(
        "/api/v1/context/search-multi",
        json={"query": "x", "projectIds": [PROJECT_A, "p-foreign"]},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "NOT_FOUND"
    assert PROJECT_A not in res.text


async def test_project_ids_bound_is_enforced(client, db):
    res = await client.post(
        "/api/v1/context/search-multi",
        json={"query": "x", "projectIds": [f"p-{i}" for i in range(11)]},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 422
