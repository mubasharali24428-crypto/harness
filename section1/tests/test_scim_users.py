"""W16 slice 3 — the SCIM user surface an IdP connector actually drives.

Covers read (list/filter/get), the two writes a deprovisioning project needs
(`active`, `userName`), the seat link (`externalId`), concurrency (`If-Match`),
refusal shapes, and — the point of the whole slice — that `active=false` really
ends the person's access through the doors slice 1 built.
"""

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.auth import get_principal
from harness_identity.crypto import KeyVault
from harness_identity.models import AuditLog, AuthSession, Base, Team, TeamMember, User
from harness_identity.scim import ScimUserLink, scim_router
from harness_identity.scim import router as api_router

MASTER_KEY = b"test-master-key-0123456789abcdef"
PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def client(factory):
    app = FastAPI()
    app.include_router(api_router)
    app.include_router(scim_router)

    @app.get("/whoami")
    async def whoami(principal=Depends(get_principal)):  # noqa: B008, ANN001
        return principal.model_dump()

    app.state.session_factory = factory
    app.state.vault = KeyVault(MASTER_KEY)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _seed_team(factory, *, members: int = 2) -> dict:
    """One team: an admin (never a SCIM target here), N members with sessions."""
    out = {"members": []}
    async with factory() as db:
        team = Team(name="scim-users")
        db.add(team)
        await db.flush()
        from decimal import Decimal

        from harness_identity.billing import BillingPlan

        db.add(BillingPlan(team_id=team.id, plan="team", monthly_budget_usd=Decimal("0")))

        out["teamId"] = team.id

        async def _person(email: str, role: str) -> dict:
            user = User(email=email, name=email.split("@")[0])
            db.add(user)
            await db.flush()
            member = TeamMember(
                team_id=team.id, user_id=user.id, display_name=user.name, role=role
            )
            db.add(member)
            token = secrets.token_urlsafe(32)
            db.add(
                AuthSession(
                    token_hash=hash_token(token),
                    user_id=user.id,
                    team_id=team.id,
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
            return {"userId": user.id, "sessionId": token}

        admin = await _person(f"admin-{secrets.token_hex(3)}@x.com", "admin")
        out["token"] = admin.pop("sessionId")
        out["userId"] = admin["userId"]
        for i in range(members):
            email = f"user{i}-{secrets.token_hex(3)}@x.com"
            out["members"].append(await _person(email, "engineer"))
        await db.commit()
    return out


async def _token(client, seed, label="bridge") -> str:
    r = await client.post(
        f"/api/v1/teams/{seed['teamId']}/scim/tokens",
        json={"label": label},
        headers={"Authorization": f"Bearer {seed['token']}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["token"]


def _scim(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _set(path: str, value: object) -> list[dict]:
    """The one operation shape a deprovisioning connector sends."""
    return [{"op": "replace", "path": path, "value": value}]


async def _audit(factory, action):
    async with factory() as db:
        return (
            (await db.execute(select(AuditLog).where(AuditLog.action == action)))
            .scalars()
            .all()
        )


# --------------------------------------------------------------------- read


async def test_list_returns_only_this_teams_members(client, factory):
    seed = await _seed_team(factory)
    other = await _seed_team(factory)
    token = await _token(client, seed)

    r = await client.get("/scim/v2/Users", headers=_scim(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:ListResponse"]
    assert body["totalResults"] == 3
    ids = {res["id"] for res in body["Resources"]}
    assert ids == {seed["userId"], *[m["userId"] for m in seed["members"]]}
    assert other["userId"] not in ids

    one = body["Resources"][0]
    assert one["schemas"] == ["urn:ietf:params:scim:schemas:core:2.0:User"]
    assert one["active"] is True
    assert one["userName"].endswith("@x.com")
    assert one["meta"]["resourceType"] == "User"
    assert one["meta"]["location"].endswith(f"/scim/v2/Users/{one['id']}")
    assert one["meta"]["etag"].startswith('W/"')
    assert "lastModified" not in one["meta"]  # not tracked; absent beats invented


async def test_pagination_is_bounded_and_honest(client, factory):
    seed = await _seed_team(factory, members=4)
    token = await _token(client, seed)
    r = await client.get("/scim/v2/Users?startIndex=3&count=2", headers=_scim(token))
    body = r.json()
    assert body["totalResults"] == 5
    assert body["startIndex"] == 3
    assert body["count"] == 2
    assert len(body["Resources"]) == 2


async def test_filter_supports_the_three_forms_connectors_send(client, factory):
    seed = await _seed_team(factory, members=1)
    token = await _token(client, seed)
    target = seed["members"][0]

    email = (await _email(client, token, target["userId"])).upper()
    r = await client.get(
        "/scim/v2/Users", params={"filter": f'userName eq "{email}"'}, headers=_scim(token)
    )
    assert r.status_code == 200
    assert [res["id"] for res in r.json()["Resources"]] == [target["userId"]]

    r = await client.get(
        "/scim/v2/Users", params={"filter": f'id eq "{target["userId"]}"'}, headers=_scim(token)
    )
    assert [res["id"] for res in r.json()["Resources"]] == [target["userId"]]

    # externalId only exists once a mutation set it.
    r = await client.get(
        "/scim/v2/Users",
        params={"filter": 'externalId eq "okta-77"'},
        headers=_scim(token),
    )
    assert r.json()["totalResults"] == 0
    await _patch(client, token, target["userId"], _set("externalId", "okta-77"))
    r = await client.get(
        "/scim/v2/Users",
        params={"filter": 'externalId eq "okta-77"'},
        headers=_scim(token),
    )
    assert [res["id"] for res in r.json()["Resources"]] == [target["userId"]]


async def _email(client, token, user_id) -> str:
    r = await client.get(f"/scim/v2/Users/{user_id}", headers=_scim(token))
    return r.json()["userName"]


async def test_unsupported_filter_is_an_scim_400(client, factory):
    seed = await _seed_team(factory)
    token = await _token(client, seed)
    r = await client.get(
        "/scim/v2/Users", params={"filter": 'name.givenName co "x"'}, headers=_scim(token)
    )
    assert r.status_code == 400
    body = r.json()["detail"]
    assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert body["status"] == "400"
    assert body["scimType"] == "invalidFilter"


async def test_get_one_user_and_the_no_oracle_404(client, factory):
    seed = await _seed_team(factory)
    other = await _seed_team(factory)
    token = await _token(client, seed)

    r = await client.get(f"/scim/v2/Users/{seed['members'][0]['userId']}", headers=_scim(token))
    assert r.status_code == 200
    assert r.json()["id"] == seed["members"][0]["userId"]

    # Another team's member id answers exactly what no id answers.
    foreign = await client.get(f"/scim/v2/Users/{other['userId']}", headers=_scim(token))
    missing = await client.get(
        "/scim/v2/Users/00000000-dead-beef-0000-000000000000", headers=_scim(token)
    )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert foreign.json()["detail"]["status"] == "404"


# -------------------------------------------------------------------- write


async def _patch(client, token, user_id, ops, headers=None):
    merged = dict(_scim(token))
    merged.update(headers or {})
    return await client.request(
        "PATCH",
        f"/scim/v2/Users/{user_id}",
        json={"schemas": [PATCH_OP], "Operations": ops},
        headers=merged,
    )


async def test_deprovision_disables_the_account_and_kills_the_seat(client, factory):
    seed = await _seed_team(factory)
    token = await _token(client, seed)
    target = seed["members"][0]

    r = await _patch(client, token, target["userId"], _set("active", False))
    assert r.status_code == 200, r.text
    assert r.json()["active"] is False

    async with factory() as db:
        user = (await db.execute(select(User).where(User.id == target["userId"]))).scalar_one()
        assert user.is_active is False
        # The team's session rows are gone, teams_api convention...
        sessions = (
            (
                await db.execute(
                    select(AuthSession).where(
                        AuthSession.user_id == target["userId"],
                        AuthSession.team_id == seed["teamId"],
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sessions == []

    # ...and even a session that had survived is refused at the door (slice 1).
    me = await client.get("/whoami", headers={"Authorization": f"Bearer {target['sessionId']}"})
    assert me.status_code == 401

    rows = await _audit(factory, "scim.user_changed")
    assert len(rows) == 1
    detail = rows[0].detail
    assert detail["kind"] == "deprovision"
    assert detail["before"] == {"active": True}
    assert detail["after"] == {"active": False}
    assert detail["refId"]
    assert rows[0].actor_kind == "system"


async def test_reactivation_is_allowed_and_audited(client, factory):
    seed = await _seed_team(factory)
    token = await _token(client, seed)
    target = seed["members"][0]
    await _patch(client, token, target["userId"], _set("active", False))
    r = await _patch(client, token, target["userId"], _set("active", True))
    assert r.status_code == 200
    assert r.json()["active"] is True
    rows = await _audit(factory, "scim.user_changed")
    assert rows[-1].detail["kind"] == "reactivate"


async def test_the_last_admin_cannot_be_deprovisioned(client, factory):
    seed = await _seed_team(factory)
    token = await _token(client, seed)

    r = await _patch(client, token, seed["userId"], _set("active", False))
    assert r.status_code == 403
    assert r.json()["detail"]["status"] == "403"
    assert "admin" in r.json()["detail"]["detail"]

    async with factory() as db:
        user = (await db.execute(select(User).where(User.id == seed["userId"]))).scalar_one()
        assert user.is_active is True
    assert await _audit(factory, "scim.user_changed") == []


async def test_rename_rejects_a_collision(client, factory):
    seed = await _seed_team(factory)
    token = await _token(client, seed)
    a, b = seed["members"][0]["userId"], seed["members"][1]["userId"]
    taken = await _email(client, token, b)

    r = await _patch(client, token, a, [{"op": "replace", "path": "userName", "value": taken}])
    assert r.status_code == 409
    assert r.json()["detail"]["scimType"] == "uniqueness"

    new = "renamed-" + secrets.token_hex(3) + "@x.com"
    r = await _patch(client, token, a, _set("userName", new.lower()))
    assert r.status_code == 200, r.text
    assert r.json()["userName"] == new.lower()
    rows = await _audit(factory, "scim.user_changed")
    assert rows[-1].detail["kind"] == "rename"


@pytest.mark.parametrize("value", ["not-an-email", "", "a b@c.com"])
async def test_user_name_must_look_like_one(client, factory, value):
    seed = await _seed_team(factory)
    token = await _token(client, seed)
    r = await _patch(
        client, token, seed["members"][0]["userId"],
        _set("userName", value),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["scimType"] == "invalidValue"


async def test_active_must_be_a_boolean(client, factory):
    seed = await _seed_team(factory)
    token = await _token(client, seed)
    r = await _patch(
        client, token, seed["members"][0]["userId"],
        _set("active", "false"),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["scimType"] == "invalidValue"


async def test_unknown_path_and_remove_op_are_refused_not_ignored(client, factory):
    seed = await _seed_team(factory)
    token = await _token(client, seed)
    r = await _patch(
        client, token, seed["members"][0]["userId"],
        [{"op": "replace", "path": "name.givenName", "value": "Z"}],
    )
    assert r.status_code == 400
    assert r.json()["detail"]["scimType"] == "invalidPath"

    r = await _patch(
        client, token, seed["members"][0]["userId"],
        [{"op": "remove", "path": "active"}],
    )
    assert r.status_code == 400


async def test_a_multi_op_patch_is_all_or_nothing(client, factory):
    seed = await _seed_team(factory)
    token = await _token(client, seed)
    target = seed["members"][0]["userId"]
    r = await _patch(
        client, token, target,
        [
            {"op": "replace", "path": "active", "value": False},
            {"op": "replace", "path": "userName", "value": "bad"},
        ],
    )
    assert r.status_code == 400
    async with factory() as db:
        user = (await db.execute(select(User).where(User.id == target))).scalar_one()
        assert user.is_active is True
    assert await _audit(factory, "scim.user_changed") == []


async def test_operations_shape_is_required(client, factory):
    seed = await _seed_team(factory)
    token = await _token(client, seed)
    r = await client.request(
        "PATCH",
        f"/scim/v2/Users/{seed['members'][0]['userId']}",
        json={"schemas": [PATCH_OP]},
        headers=_scim(token),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["scimType"] == "invalidSyntax"


async def test_if_match_is_enforced_and_the_etag_tracks_the_state(client, factory):
    seed = await _seed_team(factory)
    token = await _token(client, seed)
    target = seed["members"][0]["userId"]
    res = await client.get(f"/scim/v2/Users/{target}", headers=_scim(token))
    etag = res.json()["meta"]["etag"]

    r = await _patch(
        client, token, target,
        _set("active", False),
        headers={"If-Match": 'W/"stale"'},
    )
    assert r.status_code == 412

    r = await _patch(
        client, token, target,
        _set("active", False),
        headers={"If-Match": etag},
    )
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["etag"] != etag


async def test_put_replaces_the_three_attributes(client, factory):
    seed = await _seed_team(factory)
    token = await _token(client, seed)
    target = seed["members"][0]["userId"]

    r = await client.put(
        f"/scim/v2/Users/{target}",
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "externalId": "z"},
        headers=_scim(token),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["scimType"] == "invalidSyntax"

    new = f"put-{secrets.token_hex(3)}@x.com"
    r = await client.put(
        f"/scim/v2/Users/{target}",
        json={"userName": new, "active": True, "externalId": "entra-42"},
        headers=_scim(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["userName"] == new
    assert r.json()["externalId"] == "entra-42"
    async with factory() as db:
        link = (
            await db.execute(
                select(ScimUserLink).where(ScimUserLink.user_id == target)
            )
        ).scalar_one()
        assert link.external_id == "entra-42"
        assert link.team_id == seed["teamId"]


async def test_external_id_is_unique_per_team(client, factory):
    seed = await _seed_team(factory)
    token = await _token(client, seed)
    a, b = seed["members"][0]["userId"], seed["members"][1]["userId"]
    await _patch(client, token, a, [{"op": "replace", "path": "externalId", "value": "dup"}])
    r = await _patch(client, token, b, [{"op": "replace", "path": "externalId", "value": "dup"}])
    assert r.status_code == 409
    assert r.json()["detail"]["scimType"] == "uniqueness"


async def test_a_session_token_cannot_write_through_scim(client, factory):
    seed = await _seed_team(factory)
    r = await client.request(
        "PATCH",
        f"/scim/v2/Users/{seed['members'][0]['userId']}",
        json={"Operations": _set("active", False)},
        headers={"Authorization": f"Bearer {seed['token']}"},
    )
    assert r.status_code == 401
