"""Part 40 (session 12): session ownership transfer. The owner change is audited and atomic with
its member roles; a caller who names a stale owner gets 409 STALE_CLIENT_STATE with the current
owner (the Part 14 emitter); only a manager may transfer and the recipient must be an active member.
Org-linked step-up is covered on live PostgreSQL (test_session_owner_transfer_pg.py)."""

import pytest
from sqlalchemy import text

from tests.test_sessions_support import (
    S,
    add_member,
    audit_actions,
    bearer,
    create_session,
    err,
    make_env,
    seed_team,
)


@pytest.fixture
async def env():
    async with make_env() as (client, factory):
        team = await seed_team(factory, ("admin@a.io", "admin"), ("eng@a.io", "engineer"),
                               ("eng2@a.io", "engineer"), ("view@a.io", "viewer"))
        yield client, factory, team


async def _transfer(client, actor, sid, to, expected):
    return await client.post(f"{S}/{sid}/owner", json={"toUserId": to["userId"],
                             "expectedOwnerUserId": expected["userId"]}, headers=bearer(actor))


async def _roles(factory, sid):
    async with factory() as db:
        rows = (await db.execute(
            text("SELECT user_id, role FROM session_members WHERE session_id=:s"), {"s": sid}
        )).all()
    return {r[0]: r[1] for r in rows}


async def test_transfer_is_audited_and_the_old_owner_keeps_a_seat(env):
    client, factory, team = env
    eng, e2 = team["eng@a.io"], team["eng2@a.io"]
    sid = (await create_session(client, eng, "Handover"))["id"]
    await add_member(client, eng, sid, e2)
    r = await _transfer(client, eng, sid, e2, eng)
    assert r.status_code == 200, r.text
    assert r.json()["ownerUserId"] == e2["userId"] and r.json()["changed"] is True
    roles = await _roles(factory, sid)
    assert roles[e2["userId"]] == "owner" and roles[eng["userId"]] == "participant"
    (ev,) = await audit_actions(factory, "session.owner_transferred")
    assert ev.detail["before"] == {"ownerUserId": eng["userId"]}
    assert ev.detail["after"] == {"ownerUserId": e2["userId"]}


async def test_a_stale_expected_owner_is_refused_and_nothing_moves(env):
    client, factory, team = env
    eng, e2, adm = team["eng@a.io"], team["eng2@a.io"], team["admin@a.io"]
    sid = (await create_session(client, eng, "Stale"))["id"]
    await add_member(client, eng, sid, e2)
    r = await _transfer(client, adm, sid, e2, e2)  # the admin saw e2 as the owner; eng is
    assert r.status_code == 409 and err(r) == "STALE_CLIENT_STATE"
    assert r.json()["detail"]["currentOwnerUserId"] == eng["userId"]
    assert await audit_actions(factory, "session.owner_transferred") == []
    assert (await _roles(factory, sid))[eng["userId"]] == "owner"


async def test_only_a_manager_transfers_and_the_recipient_must_be_a_member(env):
    client, factory, team = env
    eng, e2, adm, view = (team["eng@a.io"], team["eng2@a.io"], team["admin@a.io"],
                          team["view@a.io"])
    sid = (await create_session(client, eng, "Guarded"))["id"]
    await add_member(client, eng, sid, e2)
    r = await _transfer(client, e2, sid, eng, eng)  # a participant cannot manage the session
    assert r.status_code == 403
    r = await _transfer(client, eng, sid, view, eng)  # the viewer never joined the session
    assert r.status_code == 422 and err(r) == "BAD_RECIPIENT"
    r = await _transfer(client, eng, sid, eng, eng)
    assert r.status_code == 422 and err(r) == "SELF_TRANSFER"
    r = await _transfer(client, adm, sid, e2, eng)  # an admin may manage any session
    assert r.status_code == 200, r.text
    assert r.json()["ownerUserId"] == e2["userId"]


