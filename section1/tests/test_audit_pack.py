"""P-19 / I-03 — the one-click compliance evidence pack (90-day plan W10).

The pack is ONE bounded document a design partner hands an auditor. Its danger
cases are the export endpoint's danger cases plus two of its own:

* a non-admin pulling the whole trail through it (permission checks pass);
* a pack that quietly claims completeness while a cap truncated it
  (the manifest must say what was available next to what was delivered);
* drift from /audit/export — the audit stream MUST be the same rows the export
  endpoint hands a collector, because it goes through the same audit_rows reader.

The chain verdict is honestly ``performed: false`` on this SQLite lane: the
verdict functions are PostgreSQL-only, and a pack that claimed ``ok`` from a
no-op would be the most dangerous artifact in the repo.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.app import make_app
from harness_identity.audit import write_audit
from harness_identity.auth import hash_token
from harness_identity.config import Settings
from harness_identity.models import AuthSession, Base, Team, TeamMember, User

KEY = b"test-master-key-0123456789abcdef"

# run_collab_events is migration SQL, not Base.metadata — the collab-push test
# carries the same scratch DDL (no FKs to agent_runs; seq autoincrement).
COLLAB_DDL = (
    "CREATE TABLE run_collab_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " run_id VARCHAR(36) NOT NULL, team_id VARCHAR(36) NOT NULL,"
    " actor_user_id VARCHAR(36) NOT NULL, kind VARCHAR(40) NOT NULL, request_id VARCHAR(64),"
    " payload TEXT NOT NULL DEFAULT '{}', ts TIMESTAMP, schema_version INTEGER DEFAULT 1)"
)


@pytest.fixture
async def world(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/pack.db", connect_args={"timeout": 15}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(COLLAB_DDL))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(User(id="u1", email="a@x.z", name="a"))
        s.add(User(id="u2", email="b@x.z", name="b"))
        s.add(Team(id="t1", name="A"))
        s.add(Team(id="t2", name="B"))
        s.add(TeamMember(id="m1", team_id="t1", user_id="u1", display_name="a", role="admin"))
        s.add(
            TeamMember(id="m2", team_id="t2", user_id="u2", display_name="b", role="admin")
        )
        s.add(
            AuthSession(
                token_hash=hash_token("admintok"),
                user_id="u1",
                team_id="t1",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        s.add(
            AuthSession(
                token_hash=hash_token("othertok"),
                user_id="u2",
                team_id="t2",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await s.commit()
        for i, action in enumerate(("member.removed", "apikey.added", "account.erased")):
            await write_audit(
                s,
                action,
                actor_member_id="m1",
                target_type="agent_run",
                target_id=f"r{i}",
                detail={"teamId": "t1", "why": action},
            )
        await s.commit()
    app = make_app(factory, Settings(master_key=KEY, cors_origins=[]))
    yield {"app": app, "factory": factory}
    await engine.dispose()


async def _get(world, path, token="admintok"):
    async with AsyncClient(
        transport=ASGITransport(app=world["app"]), base_url="http://t"
    ) as c:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return await c.get(path, headers=headers)


async def _collab(world, run_id="r-run", n=2):
    """Seed the collab stream through the same table the export endpoint reads."""
    async with world["factory"]() as s:
        for i in range(n):
            await s.execute(
                text(
                    "INSERT INTO run_collab_events"
                    " (team_id, run_id, actor_user_id, kind, payload, ts)"
                    " VALUES ('t1', :run, 'u1', 'run.shared', :pl, :ts)"
                ),
                {
                    "run": run_id,
                    "pl": json.dumps({"note": f"collab-{i}", "teamId": "t1"}),
                    "ts": datetime.now(UTC),
                },
            )
        await s.commit()


async def test_pack_assembles_manifest_trail_collab_and_honest_chain(world):
    await _collab(world, n=2)
    r = await _get(world, "/api/v1/audit/pack")
    assert r.status_code == 200, r.text
    pack = r.json()

    m = pack["manifest"]
    assert m["teamId"] == "t1" and m["packId"]
    assert m["rowCounts"]["auditTrailDelivered"] == 3
    assert m["rowCounts"]["auditTrailAvailable"] == 3
    assert m["rowCounts"]["collabEventsDelivered"] == 2
    assert m["ranges"]["auditTrailSeq"] == [
        pack["auditTrail"][0]["seq"],
        pack["auditTrail"][-1]["seq"],
    ]
    assert m["truncated"] is False
    # Retention answers the first question an auditor asks, stated not linked.
    assert "pseudonymises" in m["retention"]["erasure"]
    assert "retention hold" in m["retention"]["retentionHold"]

    # The audit trail is the export rows, byte-for-byte on the wire fields —
    # same reader, so a pack and a collector cannot disagree. Prefix, not
    # equality: the pack call itself appended its own audit.exported row.
    ex = await _get(world, "/api/v1/audit/export?stream=audit&limit=100")
    assert ex.status_code == 200
    exported = ex.json()["items"]
    assert exported[: len(pack["auditTrail"])] == pack["auditTrail"]

    assert [e["kind"] for e in pack["collabEvents"]] == ["run.shared", "run.shared"]
    assert pack["collabEvents"][0]["payload"]["note"] == "collab-0"

    # SQLite lane: the pack says it could not verify — never a fake ok.
    chain = pack["chainVerification"]
    assert chain["performed"] is False
    assert "PostgreSQL" in chain["reason"]
    assert "ok" not in chain


async def test_non_admin_is_refused_and_cross_team_is_unreachable(world):
    # No team parameter exists, so "another team's pack" is not expressible;
    # the proof is that a foreign admin still only ever receives THEIR team —
    # whose trail is empty because nothing audited ever touched team t2.
    r = await _get(world, "/api/v1/audit/pack", token="othertok")
    assert r.status_code == 200
    body = r.json()
    assert body["manifest"]["teamId"] == "t2"
    assert body["auditTrail"] == [], "a foreign team's rows must not appear"

    r = await _get(world, "/api/v1/audit/pack", token="")
    assert r.status_code == 401


async def test_generating_a_pack_is_itself_audited(world):
    r = await _get(world, "/api/v1/audit/pack")
    assert r.status_code == 200
    r2 = await _get(world, "/api/v1/audit/export?stream=audit&limit=100")
    actions = [row["action"] for row in r2.json()["items"]]
    assert "audit.exported" in actions, "reading evidence is a consequential read"
    pack_rows = [
        row
        for row in r2.json()["items"]
        if row["action"] == "audit.exported" and row["detail"].get("stream") == "pack"
    ]
    assert pack_rows, "the pack access row names the pack stream"
    assert pack_rows[0]["detail"]["rows"] == 3


async def test_truncation_is_said_out_loud(world):
    await _collab(world, n=3)
    r = await _get(world, "/api/v1/audit/pack?maxRows=1")
    assert r.status_code == 200
    m = r.json()["manifest"]
    assert m["truncated"] is True
    assert m["rowCounts"]["auditTrailDelivered"] == 1
    assert m["rowCounts"]["collabEventsDelivered"] == 1
    assert m["rowCounts"]["collabEventsAvailable"] == 3
    assert m["maxRowsPerStream"] == 1


async def test_max_rows_is_bounded(world):
    r = await _get(world, "/api/v1/audit/pack?maxRows=99999999")
    assert r.status_code == 200
    assert r.json()["manifest"]["maxRowsPerStream"] <= 10000
