"""S1-R05e — GET /api/v1/audit operator read surface.

Before this route the audit trail was write-only: "who stopped/started what,
who was denied" had no API answer. Contract under test: admin-only,
newest-first, bounded page, team-scoped (bound rows by membership; unbound
denial rows by the ``detail.teamId`` they carry — denials of non-members
have no member row by design).
"""

import json
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from harness_contracts import hash_token
from sqlalchemy import text

from harness_identity.models import (
    AuditLog,
    AuthSession,
    Team,
    TeamMember,
    User,
)

pytestmark = pytest.mark.filterwarnings(
    "ignore:The default datetime adapter is deprecated:DeprecationWarning"
)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_member(db, team_id: str, email: str, role: str = "engineer") -> dict:
    async with db() as session:
        user = User(email=email.lower(), name=email.split("@")[0])
        session.add(user)
        await session.flush()
        member = TeamMember(
            team_id=team_id, user_id=user.id, display_name=user.name, role=role
        )
        session.add(member)
        token = secrets.token_urlsafe(32)
        session.add(
            AuthSession(
                token_hash=hash_token(token),
                user_id=user.id,
                team_id=team_id,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await session.commit()
    return {"memberId": member.id, "userId": user.id, "token": token}


async def _seed_team(db, name: str) -> str:
    async with db() as session:
        team = Team(name=name)
        session.add(team)
        await session.commit()
        return team.id


@pytest.mark.asyncio
async def test_audit_route_scopes_rows_and_orders_newest_first(db, client):
    team_id = await _seed_team(db, "audit-team")
    admin = await _seed_member(db, team_id, "admin@audit.test", role="admin")
    engineer = await _seed_member(db, team_id, "eng@audit.test", role="engineer")
    other_team = await _seed_team(db, "other-team")
    foreign = await _seed_member(db, other_team, "foreign@audit.test", role="admin")

    async with db() as session:
        session.add_all(
            [
                # 1. ours, bound actor — visible
                AuditLog(
                    actor_member_id=admin["memberId"],
                    actor_kind="human",
                    action="invite.minted",
                    target_type="team_member",
                    target_id=engineer["memberId"],
                    detail={"teamId": team_id},
                ),
                # 2. ours, unbound actor (non-member denial) — visible via detail
                AuditLog(
                    actor_member_id=None,
                    actor_kind="human",
                    action="authz.denied",
                    target_type="agent_run",
                    target_id="run-denied",
                    detail={"teamId": team_id, "userId": "u-outsider"},
                ),
                # 3. foreign unbound denial — NOT visible
                AuditLog(
                    actor_member_id=None,
                    actor_kind="human",
                    action="authz.denied",
                    target_type="agent_run",
                    target_id="run-foreign",
                    detail={"teamId": other_team, "userId": "u-x"},
                ),
                # 4. foreign member row — NOT visible
                AuditLog(
                    actor_member_id=foreign["memberId"],
                    actor_kind="human",
                    action="run.stopped",
                    target_type="agent_run",
                    target_id="run-foreign-2",
                    detail={"teamId": other_team},
                ),
            ]
        )
        await session.commit()

    res = await client.get("/api/v1/audit", headers=_bearer(admin["token"]))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["limit"] == 50
    items = body["items"]
    assert [i["action"] for i in items] == ["authz.denied", "invite.minted"]
    assert items[0]["targetId"] == "run-denied"
    assert items[0]["actorMemberId"] is None
    assert items[0]["detail"]["userId"] == "u-outsider"
    assert items[1]["actorMemberId"] == admin["memberId"]
    assert items[1]["targetId"] == engineer["memberId"]
    assert all(i["seq"] >= 1 for i in items)


@pytest.mark.asyncio
async def test_audit_route_bounds_limit(db, client):
    team_id = await _seed_team(db, "bound-team")
    admin = await _seed_member(db, team_id, "admin2@audit.test", role="admin")

    async with db() as session:
        for n in range(5):
            session.add(
                AuditLog(
                    actor_member_id=admin["memberId"],
                    actor_kind="human",
                    action="run.started",
                    target_type="agent_run",
                    target_id=f"run-{n}",
                    detail={"teamId": team_id},
                )
            )
        await session.commit()

    res = await client.get(
        "/api/v1/audit?limit=2", headers=_bearer(admin["token"])
    )
    assert res.status_code == 200
    assert res.json()["limit"] == 2
    assert len(res.json()["items"]) == 2

    res = await client.get(
        "/api/v1/audit?limit=99999", headers=_bearer(admin["token"])
    )
    assert res.json()["limit"] == 200  # clamped

    res = await client.get("/api/v1/audit?limit=0", headers=_bearer(admin["token"]))
    assert res.json()["limit"] == 1  # floored


@pytest.mark.asyncio
async def test_audit_route_is_admin_only(db, client):
    team_id = await _seed_team(db, "deny-team")
    engineer = await _seed_member(db, team_id, "eng2@audit.test", role="engineer")
    viewer = await _seed_member(db, team_id, "viewer2@audit.test", role="viewer")

    for who in (engineer, viewer):
        res = await client.get("/api/v1/audit", headers=_bearer(who["token"]))
        assert res.status_code == 403, res.text

    res = await client.get("/api/v1/audit")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_audit_route_shows_rows_written_by_s4_raw_sql(db, client):
    """Rows written by section4's raw-SQL helper (JSON text detail, FK-bound
    actor) render through the route — the cross-section contract."""
    team_id = await _seed_team(db, "xsec-team")
    admin = await _seed_member(db, team_id, "admin3@audit.test", role="admin")

    async with db() as session:  # emulate S4's raw INSERT shape
        await session.execute(
            text(
                "INSERT INTO audit_log (actor_member_id, actor_kind, action,"
                " target_type, target_id, detail, ts)"
                " VALUES (:a, 'human', 'run.stopped', 'agent_run', 'run-s4',"
                " :d, :ts)"
            ),
            {
                "a": admin["memberId"],
                "d": json.dumps({"runId": "run-s4", "teamId": team_id}),
                "ts": datetime.now(UTC),
            },
        )
        await session.commit()

    res = await client.get("/api/v1/audit", headers=_bearer(admin["token"]))
    assert res.status_code == 200
    items = res.json()["items"]
    assert items[0]["action"] == "run.stopped"
    assert items[0]["detail"]["runId"] == "run-s4"  # parsed JSON text
