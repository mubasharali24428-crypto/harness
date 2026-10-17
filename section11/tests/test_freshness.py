"""F011 freshness-report proofs (content-addressed, never mtime-based).

The report must be exactly what an incremental re-index would do, so these
tests compare the two: index a repo, mutate it in all four ways, then assert
the freshness route names the changed/new/deleted files and that a re-index
makes the report fresh again (including after a touch-only non-edit, which
must NOT fake staleness).
"""

import asyncio
import os

from conftest import AUTH_HEADERS, seed_project

PROJECT = "p-fresh"


def _repo(tmp_path):
    root = tmp_path / "freshrero"
    root.mkdir()
    (root / "a.py").write_text("import os\n\n\ndef alpha():\n    return os.sep\n")
    (root / "b.py").write_text("def beta():\n    return 42\n")
    return root


async def _index(client, project: str, repo) -> dict:
    res = await client.post(
        "/api/v1/context/index",
        json={"repoPath": str(repo), "projectId": project},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 200, res.text
    job_id = res.json()["jobId"]
    for _ in range(200):
        st = (
            await client.get(f"/api/v1/context/index/{job_id}", headers=AUTH_HEADERS)
        ).json()
        if st["status"] in ("completed", "failed"):
            return st
        await asyncio.sleep(0.01)
    raise AssertionError("index job did not finish")


async def _freshness(client, project: str):
    return await client.get(f"/api/v1/context/freshness/{project}", headers=AUTH_HEADERS)


async def test_fresh_after_full_index(client, db, tmp_path):
    await seed_project(db, PROJECT)
    repo = _repo(tmp_path)
    await _index(client, PROJECT, repo)

    res = await _freshness(client, PROJECT)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["isFresh"] is True
    assert body["unchanged"] == 2
    assert (body["changed"], body["new"], body["deleted"]) == (0, 0, 0)
    assert body["pendingChunks"] == 0
    assert body["changes"] == []
    assert body["repoPath"] == str(repo)


async def test_edit_add_delete_are_all_reported(client, db, tmp_path):
    await seed_project(db, PROJECT)
    repo = _repo(tmp_path)
    await _index(client, PROJECT, repo)

    (repo / "a.py").write_text("import os\n\n\ndef alpha():\n    return os.getcwd()\n")
    (repo / "c.py").write_text("def gamma():\n    return 'new'\n")
    (repo / "b.py").unlink()

    body = (await _freshness(client, PROJECT)).json()
    assert body["isFresh"] is False
    by_path = {c["path"]: c for c in body["changes"]}
    assert by_path["a.py"]["status"] == "changed"
    assert by_path["a.py"]["chunksBefore"] > 0
    assert by_path["a.py"]["chunksAfter"] > 0
    assert by_path["c.py"]["status"] == "new"
    assert by_path["c.py"]["chunksBefore"] == 0
    assert by_path["b.py"]["status"] == "deleted"
    assert by_path["b.py"]["chunksAfter"] == 0
    assert body["pendingChunks"] == (
        by_path["a.py"]["chunksAfter"] + by_path["c.py"]["chunksAfter"]
    )

    # A re-index converges: the same walk reports fully fresh afterwards.
    await _index(client, PROJECT, repo)
    body2 = (await _freshness(client, PROJECT)).json()
    assert body2["isFresh"] is True
    assert body2["unchanged"] == 2  # a.py + c.py; b.py gone


async def test_touch_does_not_fake_staleness(client, db, tmp_path):
    """Content-addressed: bumping mtime with identical bytes stays fresh."""
    await seed_project(db, PROJECT)
    repo = _repo(tmp_path)
    await _index(client, PROJECT, repo)

    future = 2_000_000_000
    os.utime(repo / "a.py", (future, future))
    body = (await _freshness(client, PROJECT)).json()
    assert body["isFresh"] is True
    assert body["unchanged"] == 2


async def test_unknown_project_is_denied(client, db, tmp_path):
    await seed_project(db, "p-foreign", team_id="team-other")
    res = await _freshness(client, "p-foreign")
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "NOT_FOUND"


async def test_no_indexed_root_is_404(client, db):
    """Owned project that has never been indexed: fail closed, no path guess."""
    await seed_project(db, PROJECT)
    res = await _freshness(client, PROJECT)
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "NO_INDEXED_REPO"
