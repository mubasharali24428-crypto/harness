"""Parts 31/58: account erasure. A person can erase their own account.

The identity row is pseudonymised, never deleted: audit_log and the evidence tables reference users
and team seats with RESTRICT/ids, so deleting would either fail or detach held evidence. What the
person gets back: no way to sign in, no credentials, no personal inbox state, no readable name or
email. What stays: ids in audit and collaboration evidence and authored content (part 50's deletion
request handles the text). While a run under retention hold involves the person, erasure waits.
"""

import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity import erasure
from harness_identity import scim as _scim  # noqa: F401 — registers the SCIM tables
from harness_identity.erasure import router
from harness_identity.models import Base
from tests.test_workspaces_support import bearer, seed_team

URL = "/api/v1/account/erasure"
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
# Postgres-only migrations (BIGSERIAL, JSONB defaults) cannot run on sqlite: the minimal shape of
# the tables erasure reads stands in for them.
HAND_MADE = (
    "CREATE TABLE IF NOT EXISTS notification_outbox (id VARCHAR(36) PRIMARY KEY,"
    " to_email VARCHAR(255) NOT NULL, status VARCHAR(10) NOT NULL DEFAULT 'queued')",
    "CREATE TABLE IF NOT EXISTS run_participants (run_id VARCHAR(36) NOT NULL,"
    " team_id VARCHAR(36) NOT NULL, user_id VARCHAR(36) NOT NULL,"
    " role VARCHAR(16) NOT NULL DEFAULT 'participant')",
    "CREATE TABLE IF NOT EXISTS run_collab_events (seq INTEGER PRIMARY KEY,"
    " run_id VARCHAR(36) NOT NULL, team_id VARCHAR(36) NOT NULL,"
    " actor_user_id VARCHAR(36) NOT NULL, kind VARCHAR(40) NOT NULL DEFAULT 'x')",
)
# CHECK constraints the generic filler cannot guess.
FILL = {
    ("notification_inbox", "kind"): "budget", ("notification_inbox", "source_kind"): "team",
    ("notification_mutes", "scope_kind"): "kind", ("presence_records", "scope_kind"): "run",
    ("org_members", "role"): "member", ("org_invites", "role"): "member",
    ("policy_assignments", "level"): "org", ("policy_assignments", "role"): "viewer",
    ("team_invites", "role"): "engineer", ("ws_tickets", "role"): "engineer",
}


def _statements(path: Path) -> list[str]:
    sql = "\n".join(ln for ln in path.read_text().splitlines() if not ln.strip().startswith("--"))
    sql = re.sub(r"ADD COLUMN IF NOT EXISTS", "ADD COLUMN", sql)
    return [s.strip() for s in sql.split(";") if s.strip()]


@asynccontextmanager
async def make_env():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in HAND_MADE:
            await conn.execute(text(stmt))
        for mig in sorted(MIGRATIONS.glob("*.sql")):
            if not 5 <= int(mig.name[:4]) <= 56 or mig.name.startswith("0023"):
                continue
            for stmt in _statements(mig):  # statements sqlite cannot run are skipped
                try:
                    async with conn.begin_nested():
                        await conn.execute(text(stmt))
                except Exception:  # noqa: BLE001, S110
                    pass
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, factory
    await engine.dispose()


async def _fill(db, table: str, **known: str) -> None:
    """Insert one row into ``table`` with every required column set (known values win)."""
    def cols(sync_conn):
        insp = inspect(sync_conn)
        pk = set(insp.get_pk_constraint(table)["constrained_columns"])
        return [(c["name"], str(c["type"]).upper()) for c in insp.get_columns(table)
                if c["name"] in pk or (not c["nullable"] and c.get("default") is None)]
    values: dict[str, object] = {}
    for name, typ in await (await db.connection()).run_sync(cols):
        if name in known:
            values[name] = known[name]
        elif (table, name) in FILL:
            values[name] = FILL[(table, name)]
        elif "INT" in typ or "BOOL" in typ:
            values[name] = 0
        elif "JSON" in typ:
            values[name] = "[]"
        elif any(k in typ for k in ("DATE", "TIME", "NUMERIC")):
            values[name] = datetime(2030, 1, 1, tzinfo=UTC) + timedelta(days=30)
        else:
            values[name] = f"{name}-{known.get('user_id') or known.get('email')}"[:36]
    await db.execute(
        text(f"INSERT INTO {table} ({', '.join(values)})"
             f" VALUES ({', '.join(':' + k for k in values)})"),
        values)


async def _seed_person(db, who: dict) -> None:
    """One row of everything erasure must clear or leave, all belonging to ``who``."""
    mine = {"user_id": who["userId"], "team_id": who["teamId"], "org_id": "org-0"}
    addressed = {"email": who["email"], "to_email": who["email"], "team_id": who["teamId"],
                 "org_id": "org-0"}
    for table, _ in erasure.PURGE_BY_USER:
        if table != "auth_sessions":  # seed_team made the session
            await _fill(db, table, **mine)
    for table, _ in erasure.PURGE_BY_EMAIL:
        await _fill(db, table, **addressed)
    await db.commit()


async def _count(db, table: str, col: str, value: str) -> int:
    return (await db.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {col} = :v"),
                             {"v": value})).scalar()


@pytest.fixture
async def env():
    async with make_env() as (client, factory):
        team = await seed_team(factory, ("admin@a.io", "admin"), ("eng@a.io", "engineer"),
                               ("other@a.io", "engineer"))
        for email, who in team.items():
            if email != "teamId":
                who["email"] = email
        async with factory() as db:
            await db.execute(text("INSERT INTO organizations (id, name, slug, status, created_at)"
                                  " VALUES ('org-0', 'O0', 'o0', 'active', '2030-01-01')"))
            await _seed_person(db, team["eng@a.io"])
            await _seed_person(db, team["other@a.io"])
        yield client, factory, team


async def test_erasure_pseudonymises_the_account_and_clears_credentials(env):
    client, factory, team = env
    eng = team["eng@a.io"]
    r = await client.post(URL, json={"confirmEmail": "ENG@a.io"}, headers=bearer(eng))
    assert r.status_code == 200 and r.json() == {"erased": True}

    async with factory() as db:
        user = (await db.execute(text("SELECT email, name, is_active FROM users WHERE id = :u"),
                                 {"u": eng["userId"]})).one()
        assert user.email != "eng@a.io" and "eng" not in user.email and user.name == ""
        assert not user.is_active
        seat = (await db.execute(text("SELECT user_id, display_name, role FROM team_members"
                                      " WHERE display_name = 'Deleted user'"))).one()
        assert (seat.user_id, seat.role) == (None, "viewer")
        for table, col in erasure.PURGE_BY_USER:
            assert await _count(db, table, col, eng["userId"]) == 0, table
        for table, col in erasure.PURGE_BY_EMAIL:
            assert await _count(db, table, col, "eng@a.io") == 0, table
        audit = (await db.execute(text("SELECT action, detail FROM audit_log WHERE action ="
                                       " 'account.erased'"))).one()
        assert "eng@a.io" not in str(audit.detail)
    r = await client.post(URL, json={"confirmEmail": "eng@a.io"}, headers=bearer(eng))
    assert r.status_code == 401  # every session died with the account


async def test_erasure_touches_nobody_else(env):
    client, factory, team = env
    await client.post(URL, json={"confirmEmail": "eng@a.io"}, headers=bearer(team["eng@a.io"]))
    other = team["other@a.io"]
    async with factory() as db:
        assert (await db.execute(text("SELECT email FROM users WHERE id = :u"),
                                 {"u": other["userId"]})).scalar() == "other@a.io"
        for table, col in erasure.PURGE_BY_USER:
            assert await _count(db, table, col, other["userId"]) >= 1, table
        for table, col in erasure.PURGE_BY_EMAIL:
            assert await _count(db, table, col, "other@a.io") >= 1, table


async def test_wrong_confirmation_changes_nothing(env):
    client, factory, team = env
    eng = team["eng@a.io"]
    r = await client.post(URL, json={"confirmEmail": "someone@else.io"}, headers=bearer(eng))
    assert r.status_code == 422
    async with factory() as db:
        assert (await db.execute(text("SELECT is_active FROM users WHERE id = :u"),
                                 {"u": eng["userId"]})).scalar()
    assert (await client.post(URL, json={"confirmEmail": "eng@a.io"},
                              headers=bearer(eng))).status_code == 200


async def test_the_only_admin_cannot_leave_the_team_without_one(env):
    client, factory, team = env
    adm = team["admin@a.io"]
    r = await client.post(URL, json={"confirmEmail": "admin@a.io"}, headers=bearer(adm))
    assert r.status_code == 409 and r.json()["detail"]["error"]["code"] == "SOLE_ADMIN"
    async with factory() as db:  # a second admin makes it possible
        await db.execute(text("UPDATE team_members SET role = 'admin' WHERE user_id = :u"),
                         {"u": team["other@a.io"]["userId"]})
        await db.commit()
    r = await client.post(URL, json={"confirmEmail": "admin@a.io"}, headers=bearer(adm))
    assert r.status_code == 200


async def test_the_only_org_owner_cannot_erase_the_account(env):
    client, factory, team = env
    eng = team["eng@a.io"]
    async with factory() as db:
        await db.execute(text("INSERT INTO organizations (id, name, slug, status, created_at)"
                              " VALUES ('org-1', 'O', 'o', 'active', '2030-01-01')"))
        await _fill(db, "org_members", org_id="org-1", user_id=eng["userId"], role="owner")
        await db.commit()
    r = await client.post(URL, json={"confirmEmail": "eng@a.io"}, headers=bearer(eng))
    assert r.status_code == 409 and r.json()["detail"]["error"]["code"] == "SOLE_ADMIN"


async def test_erasure_waits_while_a_held_run_involves_the_person(env):
    client, factory, team = env
    eng, other = team["eng@a.io"], team["other@a.io"]
    async with factory() as db:
        await db.execute(text("INSERT INTO run_retention_holds"
                              " (run_id, held_by, reason, created_at)"
                              " VALUES ('run-held', 'admin', 'litigation', '2030-01-01')"))
        await db.execute(text("INSERT INTO run_collab_events (run_id, team_id, actor_user_id)"
                              " VALUES ('run-held', :t, :u)"), {"t": team["teamId"],
                                                              "u": eng["userId"]})
        await db.commit()
    r = await client.post(URL, json={"confirmEmail": "eng@a.io"}, headers=bearer(eng))
    assert r.status_code == 409 and r.json()["detail"]["error"]["code"] == "ERASURE_ON_HOLD"
    async with factory() as db:
        assert (await db.execute(text("SELECT email FROM users WHERE id = :u"),
                                 {"u": eng["userId"]})).scalar() == "eng@a.io"
        assert await _count(db, "notification_inbox", "user_id", eng["userId"]) == 1
        assert (await db.execute(text("SELECT COUNT(*) FROM audit_log WHERE action ="
                                      " 'account.erasure_deferred'"))).scalar() == 1
    # someone with no footprint in the held run is not affected by the hold
    assert (await client.post(URL, json={"confirmEmail": "other@a.io"},
                              headers=bearer(other))).status_code == 200
    async with factory() as db:  # releasing the hold lets the first request through
        await db.execute(text("DELETE FROM run_retention_holds"))
        await db.commit()
    assert (await client.post(URL, json={"confirmEmail": "eng@a.io"},
                              headers=bearer(eng))).status_code == 200


async def test_no_table_with_personal_data_is_forgotten(env):
    """A new table with a person's id or address must be purged or deliberately kept."""
    _, factory, _ = env
    known = ({t for t, _ in erasure.PURGE_BY_USER} | {t for t, _ in erasure.PURGE_BY_EMAIL}
             | set(erasure.ANONYMISED) | set(erasure.KEPT) | {"users"})
    personal = {"user_id", "email", "to_email", "actor_user_id", "author_user_id",
                "owner_user_id", "leader_user_id", "mentioned_user_id", "holder_user_id",
                "subject_user_id", "display_name"}

    def scan(sync_conn):
        insp = inspect(sync_conn)
        return {t for t in insp.get_table_names()
                if personal & {c["name"] for c in insp.get_columns(t)}}
    async with factory() as db:
        found = await (await db.connection()).run_sync(scan)
    assert found - known == set(), f"decide purge or keep for: {sorted(found - known)}"
    assert all(erasure.KEPT[t] for t in erasure.KEPT)  # every kept table says why
