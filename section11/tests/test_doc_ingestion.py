"""F020 — documentation ingestion (explicit folder / HTTP(S) URL sources).

Review gap: "Repo context ingestion exists; no automatic wiki/ticket/ADR
connectors verified." These tests prove the real slice end-to-end: docs land
in the SAME searchable per-project index under the docs/ namespace, secret
files and secret content are excluded/redacted through the F011 gates, bad
URLs fail per-URL without killing the batch, and the route is authz-guarded
with the same no-oracle 404 as every other retrieval surface.
"""

from conftest import AUTH_HEADERS, seed_project

PROJECT = "p-docs"

_FAKE_AWS = "AKIAIOSFODNN7EXAMPLE"


async def _search(client, project_id: str, query: str):
    return await client.post(
        "/api/v1/context/search",
        json={"query": query, "projectId": project_id, "topK": 8},
        headers=AUTH_HEADERS,
    )


async def test_folder_ingestion_indexes_docs_and_excludes_secrets(client, db, tmp_path):
    await seed_project(db, PROJECT)
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "guide.md").write_text("# Retry guide\n\nThe zephyr-rune pattern applies.\n")
    (docs / "notes.txt").write_text("ADR note: use bounded retries everywhere.\n")
    (docs / ".env").write_text(f"AWS_KEY={_FAKE_AWS}\n")
    (docs / "logo.png").write_bytes(b"\x89PNG fake")

    res = await client.post(
        "/api/v1/context/docs/index",
        json={"projectId": PROJECT, "folderPath": str(docs)},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["filesIndexed"] == 2
    assert body["chunksIndexed"] >= 2
    skipped_paths = [s["path"] for s in body["skipped"]]
    assert "docs/.env" in skipped_paths and "docs/logo.png" in skipped_paths
    assert _FAKE_AWS not in res.text

    found = await _search(client, PROJECT, "zephyr rune pattern guide")
    assert found.status_code == 200
    paths = [r["path"] for r in found.json()["results"]]
    assert "docs/guide.md" in paths
    assert _FAKE_AWS not in found.text


async def test_url_ingestion_fetches_scrubs_and_records_bad_urls(
    client, db, monkeypatch
):
    await seed_project(db, PROJECT)

    async def fake_fetch(url: str, *, timeout: float = 10.0) -> str:
        if url.endswith("/broken"):
            raise RuntimeError("connection refused")
        return f"# ADR 42\nRotate the {_FAKE_AWS} key quarterly.\n"

    monkeypatch.setattr("harness_context.service.fetch_url", fake_fetch)

    res = await client.post(
        "/api/v1/context/docs/index",
        json={
            "projectId": PROJECT,
            "urls": ["https://wiki.example/ADR/0042-retries", "https://wiki.example/broken"],
        },
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["filesIndexed"] == 1
    assert body["errors"] and body["errors"][0]["source"].endswith("/broken")
    assert "RuntimeError" in body["errors"][0]["error"]

    found = await _search(client, PROJECT, "rotate key quarterly adr")
    assert found.status_code == 200
    results = found.json()["results"]
    assert any(r["path"] == "docs/wiki.example/0042-retries.md" for r in results)
    # secret scrubbed BEFORE storage: neither the wire nor the stored content leaks it
    assert _FAKE_AWS not in found.text
    assert any("REDACTED" in r["content"] for r in results)


async def test_exactly_one_source_required(client, db, tmp_path):
    await seed_project(db, PROJECT)
    both = await client.post(
        "/api/v1/context/docs/index",
        json={"projectId": PROJECT, "folderPath": str(tmp_path), "urls": ["https://x.test/a"]},
        headers=AUTH_HEADERS,
    )
    neither = await client.post(
        "/api/v1/context/docs/index",
        json={"projectId": PROJECT},
        headers=AUTH_HEADERS,
    )
    for res in (both, neither):
        assert res.status_code == 422
        assert res.json()["detail"]["error"]["code"] == "INVALID_SOURCE"


async def test_non_http_urls_rejected(client, db):
    await seed_project(db, PROJECT)
    res = await client.post(
        "/api/v1/context/docs/index",
        json={"projectId": PROJECT, "urls": ["file:///etc/passwd", "ftp://x.test/a.md"]},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["error"]["code"] == "INVALID_URL"


async def test_docs_ingestion_authz_fails_closed(client, db, tmp_path):
    await seed_project(db, PROJECT)
    # no bearer → 401 before any work
    res = await client.post(
        "/api/v1/context/docs/index",
        json={"projectId": PROJECT, "folderPath": str(tmp_path)},
    )
    assert res.status_code == 401

    # foreign team's project → identical no-oracle 404
    await seed_project(db, "p-docs-foreign", team_id="team-other")
    res = await client.post(
        "/api/v1/context/docs/index",
        json={"projectId": "p-docs-foreign", "folderPath": str(tmp_path)},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "NOT_FOUND"
    assert "team-other" not in res.text
