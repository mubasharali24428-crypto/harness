"""Sprint 5 gap-close Task 42 (D2) on live PostgreSQL: removing a member who has acted.

Before the fix, `DELETE /teams/{id}/members/{userId}` answered 500 on Postgres for any member who
had ever written an audit row: `audit_log.actor_member_id` -> `team_members.id` is ON DELETE
RESTRICT (migration 0023), and SQLite (the unit-test default) does not enforce it, so only the
live B-09 journey saw it. The fix soft-removes the seat (migration 0082): the row stays so the
audit trail keeps its FK target, and the seat stops being a seat.

Opt-in like the other live-PG suites (HARNESS_TEST_PG=1).
"""

import os
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from harness_contracts import hash_token
from sqlalchemy import text
from test_policy_sockets import ORG, link_org, login
from test_policy_sockets_pg import env  # noqa: F401 - the throwaway PostgreSQL fixture

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG member soft-remove test skipped",
)


async def _seat(env, team_id: str, user_id: str, role: str) -> str:  # noqa: F811
    """A second human on the team with a live bearer token (raw rows, ORM enum NAMES)."""
    token = secrets.token_urlsafe(32)
    async with env.db() as s:
        await s.execute(text("INSERT INTO users (id, email, name, is_active)"
                             " VALUES (:u, :e, :n, true)"),
                        {"u": user_id, "e": f"{user_id}@x.test", "n": user_id})
        await s.execute(text("INSERT INTO team_members (id, team_id, user_id, display_name,"
                             " member_type, role) VALUES (:m, :t, :u, :n, 'HUMAN', :r)"),
                        {"m": f"tm-{user_id}", "t": team_id, "u": user_id, "n": user_id,
                         "r": role.upper()})
        await s.execute(text("INSERT INTO auth_sessions (token_hash, user_id, team_id, expires_at)"
                             " VALUES (:h, :u, :t, :x)"),
                        {"h": hash_token(token), "u": user_id, "t": team_id,
                         "x": datetime.now(UTC) + timedelta(days=1)})
        await s.commit()
    return token


async def _act(env, token: str) -> None:  # noqa: F811
    """A real member action that writes audit_log.actor_member_id = this seat."""
    r = await env.put("/api/v1/me/rendering", json={"rendering": "plain"},
                      headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text


async def test_member_who_acted_is_soft_removed_not_500(env):  # noqa: F811
    admin_token, alice = await login(env, "alice-d2@example.com")
    team = alice["teamId"]
    admin = {"Authorization": f"Bearer {admin_token}"}
    bob_token = await _seat(env, team, "bob-d2", "engineer")
    await _act(env, bob_token)
    async with env.db() as s:
        acted = (await s.execute(text(
            "SELECT seq, actor_member_id, action FROM audit_log WHERE actor_member_id = 'tm-bob-d2'"
        ))).all()
    assert acted, "precondition: bob's seat is an audit FK target"

    r = await env.delete(f"/api/v1/teams/{team}/members/bob-d2", headers=admin)
    assert r.status_code == 204, r.text  # was 500 RestrictViolationError before Task 42
    assert r.content == b""

    # Roster no longer lists bob.
    roster = await env.get(f"/api/v1/teams/{team}/members", headers=admin)
    assert roster.status_code == 200, roster.text
    assert "bob-d2" not in {m["userId"] for m in roster.json()["items"]}
    assert "tm-bob-d2" not in {m["id"] for m in roster.json()["items"]}

    # The old bearer is dead (session row deleted AND the seat no longer resolves).
    dead = await env.get("/api/v1/me/rendering", headers={"Authorization": f"Bearer {bob_token}"})
    assert dead.status_code == 401, dead.text

    async with env.db() as s:
        seat = (await s.execute(text(
            "SELECT user_id, removed_user_id, removed_at, display_name FROM team_members"
            " WHERE id = 'tm-bob-d2'"))).one()
        sessions = (await s.execute(text(
            "SELECT count(*) FROM auth_sessions WHERE user_id = 'bob-d2'"))).scalar_one()
        after = (await s.execute(text(
            "SELECT seq, actor_member_id, action FROM audit_log WHERE actor_member_id = 'tm-bob-d2'"
        ))).all()
        removed = (await s.execute(text(
            "SELECT detail FROM audit_log WHERE action = 'member.removed'"
            " AND target_id = 'tm-bob-d2'"))).scalar_one()
    # Row kept (FK target), seat detached from the login, who-held-it kept for forensics.
    assert seat.user_id is None
    assert seat.removed_user_id == "bob-d2"
    assert seat.removed_at is not None
    assert seat.display_name == "bob-d2"
    assert sessions == 0
    # Append-only law: bob's historical audit rows are byte-for-byte untouched.
    assert after == acted
    assert removed["removedUserId"] == "bob-d2"

    # A second DELETE is the same 404 as an unknown id.
    again = await env.delete(f"/api/v1/teams/{team}/members/bob-d2", headers=admin)
    assert again.status_code == 404, again.text

    # Bob cannot sign straight back into that team.
    back = await env.post("/api/v1/auth/login", json={"email": "bob-d2@x.test", "teamId": team})
    assert back.status_code == 403, back.text


async def test_removed_admin_no_longer_counts_toward_last_admin(env):  # noqa: F811
    admin_token, alice = await login(env, "alice-d2b@example.com")
    team = alice["teamId"]
    admin = {"Authorization": f"Bearer {admin_token}"}
    carol_token = await _seat(env, team, "carol-d2", "admin")
    await _act(env, carol_token)
    r = await env.delete(f"/api/v1/teams/{team}/members/carol-d2", headers=admin)
    assert r.status_code == 204, r.text
    # Alice is now the only ACTIVE admin: the removed admin seat must not count.
    demote = await env.patch(f"/api/v1/teams/{team}/members/{alice['userId']}",
                             json={"role": "viewer"}, headers=admin)
    assert demote.status_code == 409, demote.text
    assert demote.json()["detail"]["error"]["code"] == "LAST_ADMIN"


async def test_org_team_departure_soft_removes_an_audited_seat(env):  # noqa: F811
    """Same FK class on the org path: an audited seat used to be KEPT live (only its sessions
    died), so the person could sign back into the team. Now it is soft-removed."""
    admin_token, alice = await login(env, "alice-d2c@example.com")
    team = alice["teamId"]
    admin = {"Authorization": f"Bearer {admin_token}"}
    await link_org(env, alice)
    dan_token = await _seat(env, team, "dan-d2", "engineer")
    await _act(env, dan_token)
    r = await env.delete(f"/api/v1/orgs/{ORG}/teams/{team}/members/dan-d2", headers=admin)
    assert r.status_code == 204, r.text
    async with env.db() as s:
        seat = (await s.execute(text(
            "SELECT user_id, removed_user_id, removed_at FROM team_members WHERE id = 'tm-dan-d2'"
        ))).one()
    assert (seat.user_id, seat.removed_user_id) == (None, "dan-d2")
    assert seat.removed_at is not None
    back = await env.post("/api/v1/auth/login", json={"email": "dan-d2@x.test", "teamId": team})
    assert back.status_code == 403, back.text
