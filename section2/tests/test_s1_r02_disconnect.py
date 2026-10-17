"""S1-R02f — POST /github/disconnect revokes the caller's stored GitHub token.

The token lives encrypted in ``github_connections``; disconnect is the only
user-facing way to delete it. Scope: only the CALLER's rows are removed, the
response reports the removed count, and an unauthenticated call is rejected.
"""

import hashlib

from harness_contracts import KeyVault
from sqlalchemy import text

MASTER_KEY = b"s2-test-key-1234567890abcdef"


async def _seed_two_users(db):
    async with db() as s:
        await s.execute(
            text("INSERT INTO users (id, email, name, is_active) VALUES ('u1','dev@x.com','D',1)")
        )
        await s.execute(
            text("INSERT INTO users (id, email, name, is_active) VALUES ('u2','b@x.com','B',1)")
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
        vault = KeyVault(MASTER_KEY)
        await s.execute(
            text(
                "INSERT INTO github_connections (id, user_id, github_login,"
                " access_token_encrypted, scopes, created_at)"
                " VALUES ('gc1','u1','founder-dev',:t,'[]','2026-01-01T00:00:00Z')"
            ),
            {"t": vault.encrypt("gho_fake_token_123")},
        )
        await s.execute(
            text(
                "INSERT INTO github_connections (id, user_id, github_login,"
                " access_token_encrypted, scopes, created_at)"
                " VALUES ('gc2','u2','other-dev',:t,'[]','2026-01-01T00:00:00Z')"
            ),
            {"t": vault.encrypt("gho_other_token_456")},
        )
        await s.commit()


def _auth():
    return {"Authorization": "Bearer tok1"}


async def _connection_rows(db):
    async with db() as s:
        rows = (
            await s.execute(
                text("SELECT id, user_id FROM github_connections ORDER BY id")
            )
        ).mappings().all()
    return [(r["id"], r["user_id"]) for r in rows]


async def test_disconnect_removes_only_callers_connections(db, client):
    await _seed_two_users(db)
    r = await client.post("/api/v1/github/disconnect", headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"connected": False, "removed": 1}

    assert await _connection_rows(db) == [("gc2", "u2")]


async def test_disconnect_then_status_is_disconnected(db, client):
    await _seed_two_users(db)
    await client.post("/api/v1/github/disconnect", headers=_auth())
    r = await client.get("/api/v1/github/connection", headers=_auth())
    assert r.status_code == 200
    assert r.json()["connected"] is False


async def test_disconnect_when_never_connected_removes_nothing(db, client):
    await _seed_two_users(db)
    await client.post("/api/v1/github/disconnect", headers=_auth())
    r = await client.post("/api/v1/github/disconnect", headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"connected": False, "removed": 0}
    assert await _connection_rows(db) == [("gc2", "u2")]


async def test_disconnect_requires_auth(db, client):
    await _seed_two_users(db)
    r = await client.post("/api/v1/github/disconnect")
    assert r.status_code == 401
    assert await _connection_rows(db) == [("gc1", "u1"), ("gc2", "u2")]
