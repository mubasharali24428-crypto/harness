"""Session 11 work on a real PostgreSQL: the workspace quota race and account erasure.

Both were written and tested on sqlite. sqlite cannot show a row lock working, and its schema is
the ORM models plus the migrations sqlite happens to accept, so it cannot see the PostgreSQL-only
tables or the real foreign keys. Opt-in (HARNESS_TEST_PG=<server dsn or 1>); builds and drops its
own throwaway database and applies the whole migration chain.
"""

import asyncio
import importlib.util
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity import auth, erasure
from harness_identity.app import make_app
from harness_identity.config import Settings
from tests.test_workspaces_support import bearer, seed_team

ROOT = Path(__file__).resolve().parents[2]
ERASE = "/api/v1/account/erasure"
WS = "/api/v1/workspaces"
pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG session 11 tests skipped",
)
_spec = importlib.util.spec_from_file_location(
    "harness_migrate_s11", ROOT / "deploy" / "runners" / "migrate.py"
)
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)

_ret = importlib.util.spec_from_file_location(
    "events_retention_s11", ROOT / "deploy" / "runners" / "events_retention.py"
)
retention = importlib.util.module_from_spec(_ret)
_ret.loader.exec_module(retention)

# CHECK constraints a generic filler cannot guess.
FILL = {
    ("notification_inbox", "kind"): "budget", ("notification_inbox", "source_kind"): "team",
    ("notification_mutes", "scope_kind"): "kind", ("presence_records", "scope_kind"): "run",
    ("org_members", "role"): "member", ("org_invites", "role"): "member",
    ("policy_assignments", "level"): "org", ("policy_assignments", "role"): "viewer",
    ("team_invites", "role"): "engineer", ("ws_tickets", "role"): "engineer",
    ("notification_outbox", "status"): "queued",
}


def _server_dsn() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else "postgresql://harness:harness@127.0.0.1:5499/harness"


class Env:
    pass


@pytest.fixture
async def database():
    """A throwaway database, dropped even when the fixture that fills it fails."""
    import asyncpg

    name = f"harness_s11_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    yield name
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


@pytest.fixture
async def env(database):
    import asyncpg

    base = _server_dsn().rsplit("/", 1)[0]
    name = database
    conn = await asyncpg.connect(f"{base}/{name}")
    try:
        for path in sorted((ROOT / "section1" / "migrations").glob("*.sql")):
            for stmt in migrate_mod.split_statements(path.read_text(encoding="utf-8")):
                await conn.execute(stmt)
    finally:
        await conn.close()
    e = Env()
    e.dsn = f"{base}/{name}"
    engine = create_async_engine(e.dsn.replace("postgresql://", "postgresql+asyncpg://"))
    e.factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        master_key=b"test-master-key-0123456789abcdef", cors_origins=[], require_ownership=False
    )
    e.app = make_app(e.factory, settings)
    async with AsyncClient(transport=ASGITransport(app=e.app), base_url="http://test") as c:
        e.client = c
        yield e
    await engine.dispose()


# --------------------------------------------------------------------------- quota race


async def test_concurrent_workspace_creates_cannot_overshoot_the_quota(env):
    env.app.state.workspace_max_per_team = 3
    adm = (await seed_team(env.factory, ("admin@q.io", "admin")))["admin@q.io"]
    rs = await asyncio.gather(*(
        env.client.post(WS, json={"name": f"Race{i}", "dataResidency": "eu"}, headers=bearer(adm))
        for i in range(8)))
    assert sorted(r.status_code for r in rs) == [201] * 3 + [409] * 5, [r.text for r in rs]
    import asyncpg
    conn = await asyncpg.connect(env.dsn)
    try:
        assert await conn.fetchval("SELECT count(*) FROM workspaces") == 3
    finally:
        await conn.close()


# ------------------------------------------------------------------------ account erasure


async def fill(conn, table: str, **known) -> None:
    """Insert one row with every required column set; ``known`` wins where the column exists."""
    cols = await conn.fetch(
        "SELECT column_name, data_type, character_maximum_length AS n, is_nullable,"
        " column_default FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = $1", table)
    values: dict = {}
    for c in cols:
        name, typ = c["column_name"], c["data_type"]
        if name in known:
            values[name] = known[name]
        elif c["is_nullable"] == "NO" and c["column_default"] is None:
            if (table, name) in FILL:
                values[name] = FILL[(table, name)]
            elif typ in ("integer", "bigint", "smallint", "numeric", "double precision", "real"):
                values[name] = 0
            elif typ == "boolean":
                values[name] = False
            elif typ in ("jsonb", "json"):
                values[name] = "[]"
            elif typ == "timestamp with time zone":
                values[name] = datetime(2030, 1, 1, tzinfo=UTC) + timedelta(days=30)
            elif typ == "date":
                values[name] = date(2030, 2, 1)
            elif typ == "uuid":
                values[name] = uuid.uuid4()
            elif typ == "bytea":
                values[name] = b""
            elif typ == "ARRAY":
                values[name] = []
            else:
                tag = known.get("user_id") or known.get("email")
                values[name] = f"{name}-{tag}"[: c["n"] or 255]
    names = list(values)
    await conn.execute(
        f"INSERT INTO {table} ({', '.join(names)})"
        f" VALUES ({', '.join(f'${i + 1}' for i in range(len(names)))})", *values.values())


async def seed_person(conn, who: dict) -> None:
    mine = {"user_id": who["userId"], "team_id": who["teamId"], "org_id": "org-0"}
    addressed = {"email": who["email"], "to_email": who["email"], "team_id": who["teamId"],
                 "org_id": "org-0"}
    for table, _ in erasure.PURGE_BY_USER:
        if table != "auth_sessions":  # seed_team made the session
            await fill(conn, table, **mine)
    for table, _ in erasure.PURGE_BY_EMAIL:
        await fill(conn, table, **addressed)


@pytest.fixture
async def people(env):
    import asyncpg
    team = await seed_team(env.factory, ("admin@a.io", "admin"), ("eng@a.io", "engineer"),
                           ("other@a.io", "engineer"))
    for email, who in team.items():
        if email != "teamId":
            who["email"] = email
    conn = await asyncpg.connect(env.dsn)
    try:
        # the rows below point at parents that do not exist; this is a throwaway database
        await conn.execute("SET session_replication_role = replica")
        await fill(conn, "organizations", id="org-0", status="active")
        await seed_person(conn, team["eng@a.io"])
        await seed_person(conn, team["other@a.io"])
    finally:
        await conn.close()
    env.team = team
    return env


async def count(dsn, table, col, value) -> int:
    import asyncpg
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(f"SELECT count(*) FROM {table} WHERE {col} = $1", value)
    finally:
        await conn.close()


async def test_erasure_on_the_real_schema_clears_one_person_and_only_that_person(people):
    env, team = people, people.team
    eng, other = team["eng@a.io"], team["other@a.io"]
    r = await env.client.post(ERASE, json={"confirmEmail": "ENG@a.io"}, headers=bearer(eng))
    assert r.status_code == 200 and r.json() == {"erased": True}, r.text
    for table, col in erasure.PURGE_BY_USER:
        assert await count(env.dsn, table, col, eng["userId"]) == 0, table
        if table != "auth_sessions":
            assert await count(env.dsn, table, col, other["userId"]) >= 1, table
    for table, col in erasure.PURGE_BY_EMAIL:
        assert await count(env.dsn, table, col, "eng@a.io") == 0, table
        assert await count(env.dsn, table, col, "other@a.io") >= 1, table
    import asyncpg
    conn = await asyncpg.connect(env.dsn)
    try:
        user = await conn.fetchrow("SELECT email, name, is_active FROM users WHERE id = $1",
                                   eng["userId"])
        assert (user["name"], user["is_active"]) == ("", False) and "eng" not in user["email"]
        seat = await conn.fetchrow(
            "SELECT user_id, role FROM team_members WHERE display_name = 'Deleted user'")
        assert (seat["user_id"], seat["role"]) == (None, "viewer")
        detail = await conn.fetchval("SELECT detail::text FROM audit_log WHERE action ="
                                     " 'account.erased'")
        assert "eng@a.io" not in detail
    finally:
        await conn.close()
    again = await env.client.post(ERASE, json={"confirmEmail": "eng@a.io"}, headers=bearer(eng))
    assert again.status_code == 401  # the sessions died with the account


async def test_erasure_waits_for_a_held_run_on_the_real_schema(people):
    import asyncpg
    env, team = people, people.team
    eng = team["eng@a.io"]
    conn = await asyncpg.connect(env.dsn)
    try:
        await conn.execute("SET session_replication_role = replica")
        await fill(conn, "run_retention_holds", run_id="run-held")
        await fill(conn, "run_collab_events", run_id="run-held", team_id=team["teamId"],
                   actor_user_id=eng["userId"])
    finally:
        await conn.close()
    r = await env.client.post(ERASE, json={"confirmEmail": "eng@a.io"}, headers=bearer(eng))
    assert r.status_code == 409 and r.json()["detail"]["error"]["code"] == "ERASURE_ON_HOLD", r.text
    assert await count(env.dsn, "notification_inbox", "user_id", eng["userId"]) == 1


async def test_only_admin_is_refused_on_the_real_schema(people):
    adm = people.team["admin@a.io"]
    r = await people.client.post(ERASE, json={"confirmEmail": "admin@a.io"}, headers=bearer(adm))
    assert r.status_code == 409 and r.json()["detail"]["error"]["code"] == "SOLE_ADMIN", r.text


async def test_no_real_table_with_personal_data_is_forgotten(env):
    """The sqlite guard sees only the tables sqlite built; this one sees them all."""
    import asyncpg
    personal = ["user_id", "email", "to_email", "actor_user_id", "author_user_id",
                "owner_user_id", "leader_user_id", "mentioned_user_id", "holder_user_id",
                "subject_user_id", "display_name"]
    conn = await asyncpg.connect(env.dsn)
    try:
        found = {r["table_name"] for r in await conn.fetch(
            "SELECT DISTINCT table_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND column_name = ANY($1)", personal)}
        purge = [t for t, _ in erasure.PURGE_BY_USER] + [t for t, _ in erasure.PURGE_BY_EMAIL]
        blocking = await conn.fetch(
            "SELECT conrelid::regclass::text AS child, confrelid::regclass::text AS parent"
            " FROM pg_constraint WHERE contype = 'f' AND confdeltype IN ('a', 'r')"
            " AND confrelid::regclass::text = ANY($1)", purge)
    finally:
        await conn.close()
    known = (set(purge) | set(erasure.ANONYMISED) | set(erasure.KEPT) | {"users"})
    assert found - known == set(), f"decide purge or keep for: {sorted(found - known)}"
    # erasure deletes from the purge tables; a RESTRICT/NO ACTION foreign key into one refuses it
    assert [dict(b) for b in blocking] == []


# ------------------------------------------------------------- expired identity rows


async def test_expired_identity_rows_are_pruned_on_the_real_schema(env):
    """Two of the five tables keep expiry as a timestamp, three as ISO text: both must compare."""
    import asyncpg
    now = datetime.now(UTC)
    when = {"long": now - timedelta(days=3), "just": now - timedelta(hours=1),
            "live": now + timedelta(hours=1)}
    text_keys = (("ws_tickets", "token_hash"), ("oauth_states", "state"),
                 ("sso_login_states", "state_hash"))
    conn = await asyncpg.connect(env.dsn)
    try:
        await conn.execute("SET session_replication_role = replica")  # parents are not the point
        for tag, at in when.items():
            await fill(conn, "login_otps", id=f"otp-{tag}", email=f"{tag}@a.io", expires_at=at)
            await fill(conn, "auth_sessions", token_hash=f"sess-{tag}", expires_at=at)
            for table, key in text_keys:
                await fill(conn, table, **{key: f"{table}-{tag}", "expires_at": at.isoformat()})
        out = await retention.prune_identity_expired_once(env.factory, batch_size=100)
        assert out == dict.fromkeys(
            ("login_otps", "auth_sessions", "ws_tickets", "oauth_states", "sso_login_states"), 1)
        for table, key in (("login_otps", "id"), ("auth_sessions", "token_hash"), *text_keys):
            left = sorted(r[0].rsplit("-", 1)[-1] for r in await conn.fetch(
                f"SELECT {key} FROM {table}"))
            assert left == ["just", "live"], table
    finally:
        await conn.close()


# ------------------------------------------------------------------------- OTP races (T13b)


def _settings():
    return Settings(
        master_key=b"test-master-key-0123456789abcdef", cors_origins=[], require_ownership=False
    )


async def _issue(env, email):
    sent: list[str] = []
    await auth.issue_otp(env.factory, _settings(), lambda _e, c: sent.append(c), email)
    return sent[0]


async def _warm_pool(env, n):
    """Hold n connections at once so n requests start together (a cold pool serializes them)."""
    async def hold():
        async with env.factory() as s:
            await s.execute(text("SELECT pg_sleep(0.2)"))
    await asyncio.gather(*(hold() for _ in range(n)))


async def _verify(env, email, code):
    """A session of its own, like a request: (token, principal) or the refusal's message."""
    async with env.factory() as s:
        try:
            return await auth.verify_otp(s, _settings(), email, code)
        except HTTPException as e:
            return e.detail["error"]["message"]


async def test_parallel_redeems_of_one_code_mint_exactly_one_session(env):
    email = "race@a.io"
    async with env.factory() as s:  # the account exists already: only the code is in question
        await auth.login_or_register(s, email)
    code = await _issue(env, email)
    await _warm_pool(env, 8)
    results = await asyncio.gather(*(_verify(env, email, code) for _ in range(8)))
    assert sum(isinstance(r, tuple) for r in results) == 1, results


async def test_parallel_wrong_guesses_cannot_get_past_the_attempt_limit(env):
    email = "guess@a.io"
    code = await _issue(env, email)
    await _warm_pool(env, 15)
    results = await asyncio.gather(*(_verify(env, email, "000000") for _ in range(20)))
    assert results.count("wrong code") == 5, results  # only otp_max_attempts guesses were compared
    assert set(results) <= {"wrong code", "too many attempts", "no active code"}, results
    assert not isinstance(await _verify(env, email, code), tuple)  # the real code is dead too
