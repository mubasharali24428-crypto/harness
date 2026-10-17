"""F016 proofs: goal-driven ranked retrieval with a token budget.

Ranking is honest about its economics: candidates below threshold are
counted, not silently dropped; budget-excluded chunks are counted separately;
the reason field says whether the goal query or the derived keyword query
matched. The HTTP route exposes the same bundle in camelCase.
"""

import asyncio

from conftest import AUTH_HEADERS, seed_project

from harness_context.embeddings import HashingEmbedding
from harness_context.ranking import derive_queries, estimate_tokens, rank

PROJECT = "p-rank"


async def _rows_with_vecs(texts_and_meta):
    backend = HashingEmbedding(dims=256)
    rows = []
    for i, (path, text) in enumerate(texts_and_meta):
        [vec] = await backend.embed([text])
        rows.append(
            {
                "path": path,
                "lang": "python",
                "startLine": 1 + i * 10,
                "endLine": 9 + i * 10,
                "content": text,
                "vec": vec,
            }
        )
    return rows


async def test_goal_match_ranks_relevant_chunk_first():
    backend = HashingEmbedding(dims=256)
    rows = await _rows_with_vecs(
        [
            ("auth.py", "def login_user(): verify password and issue session token"),
            ("css.py", "def sanitize_css_colors(): normalize the palette here"),
        ]
    )
    queries = ["fix the login session token bug"]
    qvecs = await backend.embed(queries)
    bundle = rank(rows, qvecs, queries, max_tokens=8000)

    assert bundle.chunks, "expected at least one above-threshold chunk"
    assert bundle.chunks[0].path == "auth.py"
    assert bundle.chunks[0].reason == "goal"
    assert bundle.chunks[0].matched_query == queries[0]
    assert bundle.used_tokens <= 8000


async def test_budget_drops_are_counted_not_hidden():
    backend = HashingEmbedding(dims=256)
    rows = await _rows_with_vecs(
        [
            ("big.py", "login session token " * 200),
            ("small.py", "login session token ok"),
        ]
    )
    queries = ["login session token"]
    qvecs = await backend.embed(queries)
    bundle = rank(rows, qvecs, queries, max_tokens=50, min_score=0.05)

    assert bundle.scored_candidates >= 1
    assert bundle.dropped_for_budget >= 1
    assert all(c.approx_tokens > 0 for c in bundle.chunks)
    assert bundle.used_tokens <= 50


async def test_below_threshold_is_the_complement_count():
    backend = HashingEmbedding(dims=256)
    rows = await _rows_with_vecs(
        [
            ("rel.py", "authentication session management code"),
            ("rel2.py", "authentication session helpers"),
            ("far.py", "completely unrelated banana telemetry widget"),
        ]
    )
    queries = ["authentication session"]
    qvecs = await backend.embed(queries)
    bundle = rank(rows, qvecs, queries, max_tokens=8000, min_score=0.99)

    assert bundle.scored_candidates + bundle.below_threshold == len(rows)
    assert bundle.below_threshold >= 1  # 0.99 threshold excludes almost all


def test_derive_queries_adds_keyword_variant_only_when_useful():
    assert derive_queries("fix the login bug") == ["fix the login bug", "fix login bug"]
    # Single-token goal: keyword variant identical -> omitted.
    assert derive_queries("login") == ["login"]
    assert derive_queries("") == []


def test_estimate_tokens_floor():
    assert estimate_tokens("x") == 1
    assert estimate_tokens("x" * 40) == 10


# ------------------------------------------------------------- HTTP level


async def _index(client, repo) -> None:
    res = await client.post(
        "/api/v1/context/index",
        json={"repoPath": str(repo), "projectId": PROJECT},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 200, res.text
    job_id = res.json()["jobId"]
    for _ in range(200):
        st = (
            await client.get(f"/api/v1/context/index/{job_id}", headers=AUTH_HEADERS)
        ).json()
        if st["status"] in ("completed", "failed"):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("index job did not finish")


async def test_rank_over_http_wire_shape(client, db, mini_repo):
    await seed_project(db, PROJECT)
    await _index(client, mini_repo)

    res = await client.post(
        "/api/v1/context/rank",
        json={"goal": "widget quota storage", "projectId": PROJECT, "maxTokens": 600},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {
        "projectId",
        "queries",
        "maxTokens",
        "usedTokens",
        "scoredCandidates",
        "belowThreshold",
        "droppedForBudget",
        "chunks",
    }
    assert body["maxTokens"] == 600
    assert body["queries"][0] == "widget quota storage"
    assert body["usedTokens"] <= 600
    if body["chunks"]:
        chunk = body["chunks"][0]
        assert {"path", "lang", "startLine", "endLine", "content", "score",
                "approxTokens", "reason", "matchedQuery"} == set(chunk)


async def test_rank_requires_ownership(client, db):
    await seed_project(db, "p-foreign", team_id="team-other")
    res = await client.post(
        "/api/v1/context/rank",
        json={"goal": "x", "projectId": "p-foreign"},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "NOT_FOUND"
