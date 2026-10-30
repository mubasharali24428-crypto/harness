"""W16 slice 4 — a customer verifies the trail without our CLI or our database.

`POST /api/v1/audit/verify` answers the question an auditor asks after pulling an
export: *are these still the rows the chain sealed, and does the chain still
link?* The law that matters most here is the refusal. The verdict can only come
from re-deriving each hash with the same database function the trigger used, and
SQLite has no such function. Reporting `ok: true` on a lane that checked nothing
would be worse than staying silent, so it fails closed with 503.

The live-chain half of this contract (tamper detected, pruned prefix declared,
window respected) runs against a real Postgres in `test_audit_chain_pg.py`,
because that is the only place a chain exists.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.app import VERIFY_ROWS_MAX, make_app
from harness_identity.audit import write_audit
from harness_identity.auth import hash_token
from harness_identity.config import Settings
from harness_identity.models import AuthSession, Base, Team, TeamMember, User

KEY = b"test-master-key-0123456789abcdef"


@pytest.fixture
async def world(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/verify.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(User(id="u1", email="a@x.z", name="a"))
        s.add(User(id="u2", email="e@x.z", name="e"))
        s.add(Team(id="t1", name="A"))
        s.add(TeamMember(id="m1", team_id="t1", user_id="u1", display_name="a", role="admin"))
        s.add(TeamMember(id="m2", team_id="t1", user_id="u2", display_name="e", role="engineer"))
        for i in range(4):
            await write_audit(
                s,
                "member.removed",
                actor_member_id="m1",
                target_type="agent_run",
                target_id=f"r{i}",
                detail={"teamId": "t1", "n": i},
            )
        for tok, uid_ in (("admin-1", "u1"), ("eng-1", "u2")):
            s.add(
                AuthSession(
                    token_hash=hash_token(tok),
                    user_id=uid_,
                    team_id="t1",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
        await s.commit()
    app = make_app(factory, Settings(master_key=KEY, cors_origins=[]))
    yield {"app": app, "factory": factory}
    await engine.dispose()


async def req(world, method, path, token="admin-1", body=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with AsyncClient(
        transport=ASGITransport(app=world["app"]), base_url="http://t"
    ) as c:
        return await c.request(method, f"/api/v1{path}", headers=headers, json=body)


async def test_verification_refuses_instead_of_claiming_ok_without_a_chain(world):
    r = await req(world, "POST", "/audit/verify", body={})
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "AUDIT_CHAIN_UNAVAILABLE"
    assert "ok" not in json.dumps(r.json()), "a refusal must not look like a verdict"


async def test_an_engineer_cannot_ask_for_the_verdict(world):
    r = await req(world, "POST", "/audit/verify", token="eng-1", body={})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


async def test_the_window_size_is_bounded(world):
    r = await req(world, "POST", "/audit/verify", body={"maxRows": VERIFY_ROWS_MAX + 1})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_a_negative_start_is_refused(world):
    r = await req(world, "POST", "/audit/verify", body={"afterChainPos": -1})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_reading_the_verdict_is_itself_left_in_the_trail(world):
    """The refusal path still proves the endpoint is observed: a customer asking
    whether the trail was tampered with is a consequential read, and an admin
    who checks and says nothing happened must be traceable."""
    before = await _count(world, "audit.verified")
    await req(world, "POST", "/audit/verify", body={})
    assert await _count(world, "audit.verified") == before + 1


async def _count(world, action):

    async with world["factory"]() as db:
        return int(
            (
                await db.execute(
                    text("SELECT count(*) FROM audit_log WHERE action = :a"), {"a": action}
                )
            ).scalar_one()
        )
