import hashlib

import pytest
from harness_contracts import KeyVault
from sqlalchemy import text

from harness_github.client import GITHUB_API, MergeGuardViolation, _guard
from harness_github.service import GitHubService

MASTER_KEY = b"s2-test-key-1234567890abcdef"


def auth() -> dict:
    return {"Authorization": "Bearer tok1"}


def test_merge_guard_blocks_merge_urls():
    with pytest.raises(MergeGuardViolation):
        _guard("PUT", f"{GITHUB_API}/repos/org/repo/pulls/1/merge", None)


def test_merge_guard_blocks_branch_delete():
    with pytest.raises(MergeGuardViolation):
        _guard("DELETE", f"{GITHUB_API}/repos/org/repo/git/refs/heads/x", None)


def test_merge_guard_blocks_force_flag():
    with pytest.raises(MergeGuardViolation):
        _guard("POST", f"{GITHUB_API}/anything", {"force": True})


async def seed(db):
    async with db() as s:
        await s.execute(
            text(
                "INSERT INTO users (id, email, name, is_active)"
                " VALUES ('u1','dev@x.com','D',1)"
            )
        )
        await s.execute(text("INSERT INTO teams (id, name) VALUES ('t1','team')"))
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
                "INSERT INTO projects (id, team_id, name, repo_full_name, github_writes_enabled)"
                " VALUES ('p1','t1','demo','org/repo', 1)"
            )
        )
        await s.commit()


def _connect_token(db) -> str:
    return KeyVault(MASTER_KEY).encrypt("gho_fake_token_123")


async def connect_user(db):
    async with db() as s:
        await s.execute(
            text(
                "INSERT INTO github_connections (id, user_id, github_login,"
                " access_token_encrypted, scopes, created_at)"
                " VALUES ('gc1','u1','founder-dev',:t,'[]','2026-01-01T00:00:00Z')"
            ),
            {"t": _connect_token(db)},
        )
        await s.commit()


async def make_service(db, client):
    session = db()
    return GitHubService(session, KeyVault(MASTER_KEY), client.fake.transport())


async def test_full_agent_flow_end_to_end(db, client):
    await seed(db)
    await connect_user(db)
    fake = client.fake
    svc = await make_service(db, client)

    branch = await svc.createBranch("u1", "p1", "main", "agent/demo", team_id="t1")
    assert branch == {"branch": "agent/demo", "baseRef": "main", "sha": "abc123base"}

    commit = await svc.commitFiles(
        "u1",
        "p1",
        "agent/demo",
        "feat: hello",
        [{"path": "src/app.py", "content": "print(2)"}],
        team_id="t1",
    )
    assert commit["commitSha"].startswith("commit-")
    assert fake.refs["heads/agent/demo"] == commit["commitSha"]

    pr = await svc.openDraftPR(
        "u1", "p1", "agent/demo", "main", "Add hello", "AI summary here", team_id="t1"
    )
    assert pr["draft"] is True
    assert pr["number"] >= 1

    status = await svc.getPRStatus("u1", "p1", pr["number"], team_id="t1")
    assert status["state"] == "open" and status["headBranch"] == "agent/demo"

    tree = await svc.readRepoTree("u1", "p1", "main", team_id="t1")
    assert tree[0]["path"] == "src/app.py"

    file_data = await svc.readFile("u1", "p1", "main", "src/app.py", team_id="t1")
    assert file_data["content"] == "print(1)"

    pr_calls = [(m, u, b) for m, u, b in fake.requests if "/pulls" in u]
    assert all(m in {"GET", "POST"} for m, _, _ in pr_calls), "never merge-shaped PR calls"
    assert all(not (b or {}).get("force") for _, _, b in fake.requests), "no force anywhere"
    assert not any("/merge" in u for _, u, _ in fake.requests)


async def test_oauth_flow_encrypted_never_echoed(db, client):
    await seed(db)

    started = await client.get(
        "/api/v1/github/oauth/start", headers=auth(), follow_redirects=False
    )
    assert started.status_code in (301, 302, 303, 307)
    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    cb = await client.get(
        f"/api/v1/github/oauth/callback?code=abc&state={state}", follow_redirects=False
    )
    # Browser lands on the settings page; the token must appear NOWHERE.
    assert cb.headers["location"].startswith("http://localhost:3000/settings")
    assert "gho_fake_token_123" not in cb.text and "gho_fake_token_123" not in str(cb.headers)

    async with db() as s:
        stored = (
            await s.execute(text("SELECT access_token_encrypted FROM github_connections"))
        ).scalar()
    assert stored != "gho_fake_token_123"
    assert KeyVault(MASTER_KEY).decrypt(stored) == "gho_fake_token_123"

    # State is single-use: a replayed callback must be rejected outright.
    replay = await client.get(f"/api/v1/github/oauth/callback?code=abc&state={state}")
    assert replay.status_code == 400


async def test_github_call_without_connection_rejected(db, client):
    await seed(db)
    async with db() as s:
        await s.execute(text("DELETE FROM github_connections"))
        await s.commit()
    svc = await make_service(db, client)
    with pytest.raises(PermissionError, match="OAuth flow"):
        await svc.readRepoTree("u1", "p1", "main", team_id="t1")


async def test_connect_then_call_works(db, client):
    await seed(db)
    await connect_user(db)
    svc = await make_service(db, client)
    tree = await svc.readRepoTree("u1", "p1", "main", team_id="t1")
    assert tree[0]["path"] == "src/app.py"


async def test_http_endpoints_require_auth(client, db):
    await seed(db)
    r = await client.get("/api/v1/projects/p1/tree?ref=main")
    assert r.status_code == 401
