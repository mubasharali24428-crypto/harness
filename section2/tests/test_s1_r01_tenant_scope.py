"""S1-R01c — Section 2 resolve paths are team-scoped (no cross-tenant repos).

A project is only readable/writable when it belongs to the caller's team:
service-level lookups raise LookupError, HTTP answers 404, and run→PR
resolution answers null for foreign projects instead of leaking URLs.
"""

import hashlib

import pytest
from harness_contracts import KeyVault
from sqlalchemy import text

from harness_github.service import GitHubService

MASTER_KEY = b"s2-test-key-1234567890abcdef"


async def _seed(db):
    async with db() as s:
        await s.execute(
            text(
                "INSERT INTO users (id, email, name, is_active)"
                " VALUES ('u1','dev@x.com','D',1)"
            )
        )
        await s.execute(text("INSERT INTO teams (id, name) VALUES ('t1','team')"))
        await s.execute(text("INSERT INTO teams (id, name) VALUES ('t2','other')"))
        await s.execute(
            text(
                "INSERT INTO team_members (id, team_id, user_id, display_name, member_type, role)"
                " VALUES ('m1','t1','u1','D','human','admin')"
            )
        )
        await s.execute(
            text(
                "INSERT INTO auth_sessions (token_hash, user_id, team_id, expires_at)"
                " VALUES (:h,'u1','t1','2100-01-01T00:00:00Z')"
            ),
            {"h": hashlib.sha256(b"tok1").hexdigest()},
        )
        await s.execute(
            text(
                "INSERT INTO projects (id, team_id, name, repo_full_name)"
                " VALUES ('p1','t1','demo','org/repo')"
            )
        )
        await s.execute(
            text(
                "INSERT INTO projects (id, team_id, name, repo_full_name)"
                " VALUES ('p2','t2','foreign','org/foreign-repo')"
            )
        )
        await s.execute(
            text(
                "INSERT INTO github_connections (id, user_id, github_login,"
                " access_token_encrypted, scopes, created_at)"
                " VALUES ('gc1','u1','founder-dev',:t,'[]','2026-01-01T00:00:00Z')"
            ),
            {"t": KeyVault(MASTER_KEY).encrypt("gho_fake_token_123")},
        )
        await s.commit()


def _auth():
    return {"Authorization": "Bearer tok1"}


def _service(db, client):
    return GitHubService(db(), KeyVault(MASTER_KEY), client.fake.transport())


@pytest.mark.parametrize("method", ["readRepoTree", "readFile", "getPRStatus", "getPRDiff"])
async def test_foreign_project_lookup_raises(db, client, method):
    await _seed(db)
    svc = _service(db, client)
    calls = {
        "readRepoTree": ("u1", "p2", "main"),
        "readFile": ("u1", "p2", "main", "src/app.py"),
        "getPRStatus": ("u1", "p2", 1),
        "getPRDiff": ("u1", "p2", 1),
    }
    with pytest.raises(LookupError):
        await getattr(svc, method)(*calls[method], team_id="t1")


async def test_own_project_lookup_still_works(db, client):
    await _seed(db)
    svc = _service(db, client)
    tree = await svc.readRepoTree("u1", "p1", "main", team_id="t1")
    assert tree[0]["path"] == "src/app.py"


async def test_http_foreign_project_tree_answers_404(db, client):
    await _seed(db)
    r = await client.get("/api/v1/projects/p2/tree?ref=main", headers=_auth())
    assert r.status_code == 404


async def test_run_pr_id_foreign_project_answers_null(db, client):
    await _seed(db)
    async with db() as s:
        await s.execute(
            text(
                "INSERT INTO agent_runs (id, project_id, branch, state)"
                " VALUES ('run2','p2','agent/run2','pr_opened')"
            )
        )
        await s.execute(
            text(
                "INSERT INTO pull_requests (id, project_id, run_id, github_pr_number,"
                " pr_url, summary_md, status, created_at)"
                " VALUES ('pr2','p2','run2',7,'https://github.com/org/foreign-repo/pull/7',"
                "'x','pending','2026-01-01T00:00:00Z')"
            )
        )
        await s.commit()
    r = await client.get("/api/v1/projects/p2/runs/run2/pr", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["prId"] is None and body["url"] is None
