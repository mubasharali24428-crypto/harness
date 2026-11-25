"""Sprint 5 gap-close Task 42 (owner decision D2) — team seats are soft-removed.

SQLite half (the live-PG proof that an audited member no longer 500s is
test_member_soft_remove_pg.py; SQLite never enforced the RESTRICT FK, which is how D2 hid).
Pinned here: the removed row survives as a dead seat, the roster and last-admin guards ignore
it, the person can be seated again later, and account erasure reaches removed seats too.
"""

from sqlalchemy import select, text

from harness_identity.models import AuditLog, AuthSession, TeamMember
from tests.test_account_erasure import URL as ERASURE_URL
from tests.test_account_erasure import env  # noqa: F401 - erasure stack fixture
from tests.test_roster_api import (  # noqa: F401 - fixtures
    _bearer,
    _members_of,
    _seed_team,
    roster_client,
    roster_factory,
)
from tests.test_workspaces_support import bearer


async def _seat_row(factory, member_id: str) -> TeamMember:
    async with factory() as db:
        return (
            await db.execute(select(TeamMember).where(TeamMember.id == member_id))
        ).scalar_one()


async def test_removal_keeps_a_dead_seat_row(roster_factory, roster_client):  # noqa: F811
    team = await _seed_team(roster_factory, ("boss@x.com", "admin"), ("eng@x.com", "engineer"))
    boss, eng = team["boss@x.com"], team["eng@x.com"]
    # eng acted: an audit row points at the seat (the FK Postgres enforces as RESTRICT)
    async with roster_factory() as db:
        db.add(AuditLog(actor_member_id=eng["memberId"], actor_kind="human",
                        action="member.rendering_changed", target_type="team_member",
                        target_id=eng["memberId"], detail={}))
        await db.commit()

    r = await roster_client.delete(
        f"/api/v1/teams/{team['teamId']}/members/{eng['userId']}", headers=_bearer(boss["token"])
    )
    assert r.status_code == 204, r.text

    seat = await _seat_row(roster_factory, eng["memberId"])
    assert seat.removed_at is not None
    assert seat.user_id is None  # no (team, user) lookup in any service resolves it
    assert seat.removed_user_id == eng["userId"]
    assert seat.display_name == "eng"  # history still names the actor
    assert seat.role in ("engineer", "ENGINEER") or str(seat.role).lower().endswith("engineer")
    async with roster_factory() as db:
        acted = (await db.execute(
            select(AuditLog.actor_member_id).where(AuditLog.action == "member.rendering_changed")
        )).scalar_one()
        sessions = (await db.execute(
            select(AuthSession).where(AuthSession.user_id == eng["userId"])
        )).scalars().all()
    assert acted == eng["memberId"]  # append-only: the historical row is untouched
    assert sessions == []
    assert eng["memberId"] not in [m["id"] for m in await _members_of(roster_client, boss)]
    stale = await roster_client.get(
        f"/api/v1/teams/{team['teamId']}/members", headers=_bearer(eng["token"])
    )
    assert stale.status_code == 401


async def test_role_change_on_a_removed_member_is_404(roster_factory, roster_client):  # noqa: F811
    team = await _seed_team(roster_factory, ("boss@x.com", "admin"), ("eng@x.com", "engineer"))
    boss, eng = team["boss@x.com"], team["eng@x.com"]
    url = f"/api/v1/teams/{team['teamId']}/members/{eng['userId']}"
    assert (await roster_client.delete(url, headers=_bearer(boss["token"]))).status_code == 204
    r = await roster_client.patch(url, json={"role": "admin"}, headers=_bearer(boss["token"]))
    assert r.status_code == 404, r.text
    seat = await _seat_row(roster_factory, eng["memberId"])
    assert str(seat.role).lower().endswith("engineer")  # the dead seat was not promoted


async def test_removed_admin_does_not_count_as_another_admin(
    roster_factory, roster_client  # noqa: F811
):
    team = await _seed_team(roster_factory, ("a@x.com", "admin"), ("b@x.com", "admin"))
    a, b = team["a@x.com"], team["b@x.com"]
    tid = team["teamId"]
    assert (await roster_client.delete(
        f"/api/v1/teams/{tid}/members/{b['userId']}", headers=_bearer(a["token"])
    )).status_code == 204
    # b's dead row still says role=admin; a is nevertheless the LAST active admin.
    demote = await roster_client.patch(
        f"/api/v1/teams/{tid}/members/{a['userId']}", json={"role": "viewer"},
        headers=_bearer(a["token"]),
    )
    assert demote.status_code == 409, demote.text
    assert demote.json()["detail"]["error"]["code"] == "LAST_ADMIN"
    leave = await roster_client.delete(
        f"/api/v1/teams/{tid}/members/{a['userId']}", headers=_bearer(a["token"])
    )
    assert leave.status_code == 409
    assert leave.json()["detail"]["error"]["code"] == "LAST_ADMIN"


async def test_a_removed_person_can_be_seated_again(roster_factory, roster_client):  # noqa: F811
    team = await _seed_team(roster_factory, ("boss@x.com", "admin"), ("eng@x.com", "engineer"))
    boss, eng = team["boss@x.com"], team["eng@x.com"]
    url = f"/api/v1/teams/{team['teamId']}/members/{eng['userId']}"
    assert (await roster_client.delete(url, headers=_bearer(boss["token"]))).status_code == 204
    # what invite redemption does: a FRESH seat row (the dead one is never reactivated)
    async with roster_factory() as db:
        fresh = TeamMember(team_id=team["teamId"], user_id=eng["userId"],
                           display_name="eng", role="viewer")
        db.add(fresh)
        await db.commit()
        fresh_id = fresh.id
    roster = await _members_of(roster_client, boss)
    assert [m["id"] for m in roster if m["userId"] == eng["userId"]] == [fresh_id]
    # and the new seat is removable in turn (the dead one does not shadow it)
    assert (await roster_client.delete(url, headers=_bearer(boss["token"]))).status_code == 204
    assert eng["userId"] not in [m["userId"] for m in await _members_of(roster_client, boss)]


async def test_erasure_also_anonymises_seats_removed_earlier(env):  # noqa: F811
    client, factory, team = env
    eng = team["eng@a.io"]
    async with factory() as db:
        await db.execute(text(
            "INSERT INTO team_members (id, team_id, user_id, removed_user_id, removed_at,"
            " display_name, member_type, role, approval_rendering, created_at)"
            " VALUES ('tm-old', :t, NULL, :u, '2030-01-01', 'eng-old', 'HUMAN', 'ENGINEER',"
            " 'technical', '2030-01-01')"), {"t": eng["teamId"], "u": eng["userId"]})
        await db.commit()
    r = await client.post(ERASURE_URL, json={"confirmEmail": "eng@a.io"}, headers=bearer(eng))
    assert r.status_code == 200, r.text
    async with factory() as db:
        old = (await db.execute(text(
            "SELECT user_id, removed_user_id, display_name FROM team_members WHERE id = 'tm-old'"
        ))).one()
    assert tuple(old) == (None, None, "Deleted user")
