"""W16 slice 4 — the SIEM sink an admin configures, and what it must never do.

A sink is a per-team outbound collector: URL + shared secret (Fernet at rest,
hint only on reads) + a delivery cursor. These tests pin the administration
contract; `test_siem_push.py` pins the delivery loop.

The bounds that matter here are the ones a customer cannot see coming: an
operator allow-list on destinations, a secret that never returns, a cursor that
starts at the newest row unless the admin deliberately asks for history, and a
paused sink that stays paused instead of firing at a collector nobody enabled.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.app import make_app
from harness_identity.audit import write_audit
from harness_identity.auth import hash_token
from harness_identity.config import Settings
from harness_identity.crypto import KeyVault
from harness_identity.models import AuthSession, Base, Team, TeamMember, User
from harness_identity.siem_sink import SiemSink

KEY = b"test-master-key-0123456789abcdef"
URL = "https://siem.example.com/ingest/harness"
SECRET = "collector-shared-secret-0123456789"


@pytest.fixture
def allowed_hosts(monkeypatch):
    monkeypatch.setenv("HARNESS_SIEM_ALLOWED_HOSTS", "siem.example.com,logs.internal")


@pytest.fixture
async def world(tmp_path, allowed_hosts):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/siem.db", connect_args={"timeout": 15}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(User(id="u1", email="a@x.z", name="a"))
        s.add(User(id="u2", email="e@x.z", name="e"))
        s.add(Team(id="t1", name="A"))
        s.add(Team(id="t2", name="B"))
        s.add(TeamMember(id="m1", team_id="t1", user_id="u1", display_name="a", role="admin"))
        s.add(TeamMember(id="m2", team_id="t1", user_id="u2", display_name="e", role="engineer"))
        s.add(TeamMember(id="m3", team_id="t2", user_id="u2", display_name="e", role="admin"))
        from decimal import Decimal

        from harness_identity.billing import BillingPlan

        for tid in ("t1", "t2"):
            s.add(BillingPlan(team_id=tid, plan="team", monthly_budget_usd=Decimal("0")))
        sessions = (("admin-1", "u1", "t1"), ("eng-1", "u2", "t1"), ("admin-2", "u2", "t2"))
        for tok, uid_, tid in sessions:
            s.add(
                AuthSession(
                    token_hash=hash_token(tok),
                    user_id=uid_,
                    team_id=tid,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
        await s.commit()
        for i in range(3):
            await write_audit(
                s,
                "member.removed",
                actor_member_id="m1",
                target_type="agent_run",
                target_id="r1",
                detail={"teamId": "t1", "n": i},
            )
        await s.commit()
    app = make_app(factory, Settings(master_key=KEY, cors_origins=[]))
    yield {"app": app, "factory": factory, "vault": app.state.vault}
    await engine.dispose()


async def req(world, method, path, token="admin-1", body=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with AsyncClient(
        transport=ASGITransport(app=world["app"]), base_url="http://t"
    ) as c:
        return await c.request(method, f"/api/v1{path}", headers=headers, json=body)


def _body(**over):
    return {"label": "splunk prod", "collectorUrl": URL, "sharedSecret": SECRET, **over}


# ------------------------------------------------------------------- create


async def test_admin_creates_a_sink_and_the_secret_is_not_echoed(world):
    r = await req(world, "POST", "/teams/t1/siem/sinks", body=_body())
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["collectorUrl"] == URL
    assert created["label"] == "splunk prod"
    assert "sharedSecret" not in created and "secretEncrypted" not in json.dumps(created)
    assert created["secretHint"].endswith(SECRET[-4:])
    assert created["cursor"] == 3  # newest existing row: no history dump by default
    assert created["active"] is True

    async with world["factory"]() as db:
        row = (await db.execute(select(SiemSink).where(SiemSink.id == created["id"]))).scalar_one()
        vault = KeyVault(KEY)
        # Stored as ciphertext, and the ciphertext decrypts to what was given.
        assert row.secret_encrypted != SECRET
        assert vault.decrypt(row.secret_encrypted) == SECRET


async def test_the_cursor_can_start_at_the_earliest_row_when_asked(world):
    r = await req(world, "POST", "/teams/t1/siem/sinks", body=_body(startFrom="earliest"))
    assert r.status_code == 201, r.text
    assert r.json()["cursor"] == 0


async def test_a_destination_the_operator_has_not_allow_listed_is_refused(world):
    r = await req(world, "POST", "/teams/t1/siem/sinks", body=_body(collectorUrl="https://evil.example.com/ingest"))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_SIEM_URL"


@pytest.mark.parametrize(
    "url,code",
    [
        ("http://siem.example.com/ingest", "INVALID_SIEM_URL"),  # plaintext
        ("https://siem.example.com:8443/ingest", "INVALID_SIEM_URL"),  # odd port
        ("https://127.0.0.1/ingest", "INVALID_SIEM_URL"),  # loopback
        ("https://169.254.169.254/latest/meta-data", "INVALID_SIEM_URL"),  # link-local
        ("https://siem.example.com/ingest?token=1", "INVALID_SIEM_URL"),  # query
        ("https://user:pw@siem.example.com/ingest", "INVALID_SIEM_URL"),  # userinfo
        ("https://localhost/ingest", "INVALID_SIEM_URL"),  # local name
    ],
)
async def test_ssrf_and_transport_shapes_are_all_refused(world, url, code):
    r = await req(world, "POST", "/teams/t1/siem/sinks", body=_body(collectorUrl=url))
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == code


async def test_a_short_secret_is_refused(world):
    r = await req(world, "POST", "/teams/t1/siem/sinks", body=_body(sharedSecret="abc"))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------- authz and scope


async def test_an_engineer_cannot_see_the_collectors(world):
    r = await req(world, "GET", "/teams/t1/siem/sinks", token="eng-1")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


async def test_a_foreign_team_is_answered_like_an_unknown_one(world):
    created = (await req(world, "POST", "/teams/t1/siem/sinks", body=_body())).json()
    # u2 is admin of t2, not t1 — the same answer as a sink that does not exist.
    missing = await req(
        world, "GET", f"/teams/t1/siem/sinks/{created['id']}", token="admin-2"
    )
    unknown = await req(
        world, "GET", "/teams/t1/siem/sinks/00000000-dead-beef-0000-0000-000000000000",
        token="admin-2",
    )
    assert missing.status_code == unknown.status_code == 404
    assert missing.json() == unknown.json()


async def test_listing_two_teams_never_shares_a_sink(world):
    await req(world, "POST", "/teams/t1/siem/sinks", body=_body())
    other = await req(
        world,
        "POST",
        "/teams/t2/siem/sinks",
        token="admin-2",
        body=_body(label="other team", sharedSecret="other-collector-secret-999"),
    )
    assert other.status_code == 201, other.text
    mine = (await req(world, "GET", "/teams/t1/siem/sinks")).json()["items"]
    theirs = (await req(world, "GET", "/teams/t2/siem/sinks", token="admin-2")).json()["items"]
    assert len(mine) == 1 and len(theirs) == 1
    assert mine[0]["id"] != theirs[0]["id"]


async def test_a_team_holds_a_bounded_number_of_sinks(world):
    from harness_identity.siem_sink import MAX_SINKS_PER_TEAM

    for i in range(MAX_SINKS_PER_TEAM):
        r = await req(world, "POST", "/teams/t1/siem/sinks", body=_body(label=f"s{i}"))
        assert r.status_code == 201, r.text
    over = await req(world, "POST", "/teams/t1/siem/sinks", body=_body(label="one too many"))
    assert over.status_code == 409
    assert over.json()["error"]["code"] == "LIMIT_EXCEEDED"


# ------------------------------------------------------------- patch, delete


async def test_patch_toggles_active_with_optimistic_version(world):
    created = (await req(world, "POST", "/teams/t1/siem/sinks", body=_body())).json()
    r = await req(
        world,
        "PATCH",
        f"/teams/t1/siem/sinks/{created['id']}",
        body={"active": False, "expectedVersion": created["version"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["active"] is False
    assert r.json()["version"] == created["version"] + 1

    stale = await req(
        world,
        "PATCH",
        f"/teams/t1/siem/sinks/{created['id']}",
        body={"active": True, "expectedVersion": created["version"]},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"


async def test_every_sink_change_leaves_an_audit_row(world):
    created = (await req(world, "POST", "/teams/t1/siem/sinks", body=_body())).json()
    await req(
        world,
        "PATCH",
        f"/teams/t1/siem/sinks/{created['id']}",
        body={"active": False, "expectedVersion": created["version"]},
    )
    await req(world, "DELETE", f"/teams/t1/siem/sinks/{created['id']}")
    async with world["factory"]() as db:
        actions = [
            row[0]
            for row in (
                await db.execute(text("SELECT action FROM audit_log ORDER BY seq ASC"))
            ).all()
        ]
    assert [a for a in actions if a.startswith("siem.")] == [
        "siem.created",
        "siem.updated",
        "siem.deleted",
    ]
    async with world["factory"]() as db:
        assert (
            await db.execute(select(SiemSink).where(SiemSink.id == created["id"]))
        ).scalar_one_or_none() is None


async def test_the_lag_is_reported_so_an_operator_sees_a_stalled_collector(world):
    created = (await req(world, "POST", "/teams/t1/siem/sinks", body=_body())).json()
    # The sink's own `siem.created` row is part of the team's trail, so it is
    # pending from the start: a collector that never hears about the collector
    # would be a hole in the evidence the SIEM exists to hold.
    assert created["pendingRows"] == 1
    async with world["factory"]() as db:
        await write_audit(
            db,
            "member.removed",
            actor_member_id="m1",
            target_type="agent_run",
            target_id="r1",
            detail={"teamId": "t1", "n": 99},
        )
        await db.commit()
    listed = (await req(world, "GET", "/teams/t1/siem/sinks")).json()["items"][0]
    assert listed["cursor"] == created["cursor"]
    assert listed["pendingRows"] == created["pendingRows"] + 1
