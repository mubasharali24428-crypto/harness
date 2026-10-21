"""Part 40 (session 12) on live PostgreSQL: an org-linked team's session ownership transfer needs a
fresh step-up (the role.change action), a stale expected owner is refused, and the transfer writes
its audit row. Opt-in like the other live-PG suites (HARNESS_TEST_PG=1)."""

import os

import pytest
from sqlalchemy import text
from test_policy_sockets import ORG, login
from test_policy_sockets_pg import env  # noqa: F401 - the throwaway PostgreSQL fixture

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG owner transfer test skipped",
)


async def test_org_linked_owner_transfer_needs_step_up_and_is_audited(env):  # noqa: F811
    token, alice = await login(env, "alice@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    async with env.db() as s:
        await s.execute(text("UPDATE teams SET org_id = :o WHERE id = :t"),
                        {"o": ORG, "t": alice["teamId"]})
        await s.execute(text("INSERT INTO org_members (org_id, user_id, role, created_at)"
                             " VALUES (:o, :u, 'owner', now())"), {"o": ORG, "u": alice["userId"]})
        await s.execute(text("INSERT INTO users (id, email, name, is_active)"
                             " VALUES ('bob-s12', 'bob-s12@x.test', 'bob', true)"))
        await s.execute(text("INSERT INTO team_members (id, team_id, user_id, display_name,"
                             " member_type, role) VALUES ('tm-bob-s12', :t, 'bob-s12', 'bob',"
                             " 'HUMAN', 'ENGINEER')"), {"t": alice["teamId"]})
        await s.commit()

    r = await env.post("/api/v1/sessions", json={"title": "Handover"}, headers=headers)
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]
    r = await env.post(f"/api/v1/sessions/{sid}/members",
                       json={"userId": "bob-s12", "role": "participant"}, headers=headers)
    assert r.status_code in (200, 201), r.text
    body = {"toUserId": "bob-s12", "expectedOwnerUserId": alice["userId"]}

    refused = await env.post(f"/api/v1/sessions/{sid}/owner", json=body, headers=headers)
    assert refused.status_code == 403
    assert refused.json()["detail"]["error"]["code"] == "STEPUP_REQUIRED"

    assert (await env.post("/api/v1/stepup/verify", json={"code": "000000"},
                           headers=headers)).status_code == 200
    done = await env.post(f"/api/v1/sessions/{sid}/owner", json=body, headers=headers)
    assert done.status_code == 200, done.text
    assert done.json()["ownerUserId"] == "bob-s12"

    stale = await env.post(f"/api/v1/sessions/{sid}/owner", json={
        "toUserId": alice["userId"], "expectedOwnerUserId": alice["userId"]}, headers=headers)
    assert stale.status_code == 409
    assert stale.json()["detail"]["error"]["code"] == "STALE_CLIENT_STATE"
    assert stale.json()["detail"]["currentOwnerUserId"] == "bob-s12"

    async with env.db() as s:
        audited = (await s.execute(text(
            "SELECT count(*) FROM audit_log WHERE action = 'session.owner_transferred'"
            " AND target_id = :s"), {"s": sid})).scalar_one()
        owner = (await s.execute(text(
            "SELECT owner_user_id FROM collab_sessions WHERE id = :s"), {"s": sid})).scalar_one()
    assert audited == 1 and owner == "bob-s12"
