"""M12-W2.1 lane F1 tests (Squad ALPHA) — H-019 + H-020, section1.

H-019: every error leaves as the FROZEN error_body() envelope
``{"error": {code, message}}`` with a mirrored ``detail`` field:
  - RequestValidationError (FastAPI 422) no longer escapes as {"detail": [...]}
  - Starlette HTTPException string details normalize
  - contract-shaped detail passthrough stays byte-for-byte
  - unhandled exceptions become 500 INTERNAL_ERROR envelopes

H-020: Idempotency-Key replay on POST /api/v1/keys — same key twice =>
identical status+body and a SINGLE minted key row; different key (or no
key) => separate executions. Storage is exercised through a fake asyncpg
pool over the REAL middleware code path; migration 0015 defines the table.

Self-contained by design (conftest.py is FROZEN): fixtures are local and the
routers mount on scratch FastAPI apps, exactly like test_api_keys_routes.py.
Sessions are seeded straight into the store with contract-shaped token hashes
so no login route is needed. Everything runs offline on aiosqlite.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.api_keys import router as api_keys_router
from harness_identity.app import make_app
from harness_identity.config import Settings
from harness_identity.crypto import KeyVault
from harness_identity.envelope_middleware import (
    IdempotencyMiddleware,
    install_error_envelope,
    route_template,
)
from harness_identity.models import AuthSession, Base, Team, TeamMember, User

TEST_MASTER_KEY = b"test-master-key-0123456789abcdef"

# gitleaks:allow — fake fixture value, never a real credential
RAW_KEY = "sk-f1-test-not-a-real-secret"


# --------------------------------------------------------------- fixtures


@pytest.fixture
async def f1_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed_user(factory, email: str, *, role: str = "admin") -> dict:
    """Create team/user/membership/session rows directly; return ids + bearer."""
    import secrets

    async with factory() as db:
        team = Team(name=f"team-{email}")
        db.add(team)
        await db.flush()
        user = User(email=email.lower(), name=email.split("@")[0])
        db.add(user)
        await db.flush()
        db.add(
            TeamMember(team_id=team.id, user_id=user.id, display_name=user.name, role=role)
        )
        token = secrets.token_urlsafe(32)
        db.add(
            AuthSession(
                token_hash=hash_token(token),
                user_id=user.id,
                team_id=team.id,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await db.commit()
    return {"teamId": team.id, "userId": user.id, "token": token}


@pytest.fixture
async def env_client(f1_factory):
    """Full identity app + envelope handlers (H-019 assertions)."""
    app = make_app(f1_factory, Settings(master_key=TEST_MASTER_KEY, cors_origins=[]))
    install_error_envelope(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class FakeAsyncPgPool:
    """Minimal asyncpg-pool stand-in driving the middleware's REAL SQL paths.

    execute()/fetchrow()/acquire() mimic the asyncpg API closely enough to
    validate statement shape and reserve/lookup/store semantics without a
    live Postgres (unit lane; the compose PG is not guaranteed here).
    """

    def __init__(self):
        self.rows = {}
        # Strict JSONB typing, asyncpg-style: the column is jsonb, so a
        # non-JSON string (e.g. the old '' reserve literal) must raise —
        # exactly what asyncpg does with InvalidTextRepresentationError.
        # Without this validator the fake pool silently accepted '' and kept
        # the F1 reserve blocker green in unit lanes while failing on real PG.
        self.jsonb_validator = _JsonbValidator()

    class _Conn:
        def __init__(self, pool):
            self._pool = pool

        async def fetchrow(self, sql, *args):
            stmt = " ".join(sql.split())
            if stmt.startswith("SELECT"):
                rec = self._pool.rows.get(args[0])
                return None if rec is None else dict(rec)
            if stmt.startswith("INSERT"):
                fp = args[0]
                if fp in self._pool.rows:
                    return None  # ON CONFLICT DO NOTHING
                literal = self._extract_jsonb_literal(stmt)
                if literal.startswith("$"):
                    payload = args[int(literal.lstrip("$").split("::")[0]) - 1]
                else:
                    payload = literal.strip("'")
                self._pool.jsonb_validator.validate(payload)
                self._pool.rows[fp] = {
                    "fingerprint": fp,
                    "status_code": 0,
                    "response_body": payload,
                }
                return {"fingerprint": fp}
            if stmt.startswith("UPDATE"):
                rec = self._pool.rows.get(args[0])
                if rec is not None:
                    self._pool.jsonb_validator.validate(str(args[2]))
                    rec["status_code"], rec["response_body"] = args[1], str(args[2])
                return None
            if stmt.startswith("DELETE"):
                self._pool.rows.pop(args[0], None)
                return None
            raise AssertionError(f"unexpected SQL: {stmt[:80]}")

        @staticmethod
        def _extract_jsonb_literal(sql: str):
            import re

            m = re.search(r"VALUES\s*\(([^)]*)\)", " ".join(sql.split()))
            if not m:
                raise AssertionError(f"no VALUES clause: {sql[:120]}")
            lit = [c.strip() for c in m.group(1).split(",")][2]  # response_body
            if not (lit.startswith("$") or lit.startswith("'")):
                raise AssertionError(f"unrecognized placeholder {lit!r}: {sql[:120]}")
            return lit

        async def execute(self, sql, *args):
            await self.fetchrow(sql, *args)

    def acquire(self):
        return _AcquireCtx(self._Conn(self))


class _JsonbValidator:
    """Rejects anything Postgres JSONB would reject (asyncpg strict typing).

    Mirrors asyncpg's InvalidTextRepresentationError for empty/non-JSON
    strings; valid JSON passes through unchanged.
    """

    def validate(self, value: str) -> None:
        try:
            json.loads(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"invalid input syntax for type json ({value!r})"
            ) from exc


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


async def _noop_asgi(scope, receive, send):
    """Placeholder ASGI app for direct storage-helper probes; never runs."""
    raise AssertionError("middleware __call__ must not execute in helper probes")


@pytest.fixture
async def idem_client(f1_factory):
    """Scratch app mounting ONLY the keys router + envelope handlers +
    IdempotencyMiddleware over the fake pool (mirrors the deploy wiring)."""
    app = FastAPI()
    app.include_router(api_keys_router)
    app.state.session_factory = f1_factory
    app.state.vault = KeyVault(TEST_MASTER_KEY)
    install_error_envelope(app)
    app.add_middleware(IdempotencyMiddleware)
    app.state.idempotency_pool = FakeAsyncPgPool()
    app.state.idempotency_table = "idempotency_keys"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.app = app  # test handle (same pattern as the frozen conftest)
        yield client


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


KEY_PAYLOAD = {"label": "prod key", "provider": "openai", "key": RAW_KEY}


# --------------------------------------------------------------- H-019


async def test_validation_error_returns_unified_envelope(env_client):
    """Invalid payload => 422 in the contract envelope (+ legacy mirror),
    never the bare {"detail": [...]} FastAPI default."""
    r = await env_client.post("/api/v1/auth/request-login", json={"wrongField": 1})
    assert r.status_code == 422
    body = r.json()
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == "VALIDATION_ERROR"
    # Backward-compat mirror: identical to the pre-unification detail shape.
    assert body["detail"]["error"]["code"] == "VALIDATION_ERROR"
    assert "email" in body["error"]["message"]  # loc names the failing field


async def test_http_exception_string_detail_normalized(f1_factory):
    """Plain-string HTTPException details normalize into the envelope."""
    from fastapi import HTTPException

    app = make_app(f1_factory, Settings(master_key=TEST_MASTER_KEY, cors_origins=[]))
    install_error_envelope(app)

    @app.get("/api/v1/__f1_plain403")
    async def plain_403():
        raise HTTPException(status_code=403, detail="admin only")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/v1/__f1_plain403")
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "REQUEST_ERROR"
    assert body["error"]["message"] == "admin only"
    assert body["detail"]["error"] == body["error"]


async def test_contract_shaped_detail_passes_through_unchanged(env_client):
    """Routes raising error_body(...) inside detail keep their exact code."""
    r = await env_client.post("/api/v1/auth/login", json={"email": "x@example.com"})
    # C-001: require_ownership defaults ON => OWNERSHIP_REQUIRED envelope.
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "OWNERSHIP_REQUIRED"
    assert body["detail"]["error"]["code"] == "OWNERSHIP_REQUIRED"


async def test_unhandled_exception_becomes_envelope_500(f1_factory):
    """A raising route must answer an envelope, never a stack trace."""
    app = make_app(f1_factory, Settings(master_key=TEST_MASTER_KEY, cors_origins=[]))
    install_error_envelope(app)

    @app.get("/api/v1/__f1_boom")
    async def boom():
        raise RuntimeError("kaboom")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/v1/__f1_boom")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"


def test_route_template_collapses_ids():
    assert (
        route_template("/api/v1/orchestrator/runs/run-9f2/start")
        == "/api/v1/orchestrator/runs/{id}/start"
    )
    assert route_template("/api/v1/keys") == "/api/v1/keys"
    assert (
        route_template("/api/v1/meetings/42/messages") == "/api/v1/meetings/{id}/messages"
    )


# --------------------------------------------------------------- H-020


async def test_same_key_replays_identical_response_single_side_effect(idem_client):
    admin = await _seed_user(idem_client.app.state.session_factory, "f1a@x.com")
    headers = {**_bearer(admin["token"]), "Idempotency-Key": "mint-once"}

    r1 = await idem_client.post("/api/v1/keys", json=KEY_PAYLOAD, headers=headers)
    assert r1.status_code == 201, r1.text
    first_id = r1.json()["id"]

    r2 = await idem_client.post("/api/v1/keys", json=KEY_PAYLOAD, headers=headers)
    assert r2.status_code == 201
    assert r2.json() == r1.json()  # identical cached response...
    assert r2.json()["id"] == first_id  # ...same key minted once...
    assert r2.headers.get("idempotent-replay") == "true"  # ...served from cache

    listing = await idem_client.get("/api/v1/keys", headers=_bearer(admin["token"]))
    ids = [item["id"] for item in listing.json()["items"]]
    assert ids.count(first_id) == 1  # single side effect


async def test_different_key_executes_separately(idem_client):
    admin = await _seed_user(idem_client.app.state.session_factory, "f1b@x.com")
    base = _bearer(admin["token"])

    ra = await idem_client.post(
        "/api/v1/keys", json=KEY_PAYLOAD, headers={**base, "Idempotency-Key": "key-A"}
    )
    rb = await idem_client.post(
        "/api/v1/keys", json=KEY_PAYLOAD, headers={**base, "Idempotency-Key": "key-B"}
    )
    assert ra.status_code == rb.status_code == 201
    assert ra.json()["id"] != rb.json()["id"]  # separate executions
    assert ra.headers.get("idempotent-replay") == "false"
    assert rb.headers.get("idempotent-replay") != "true"

    listing = await idem_client.get("/api/v1/keys", headers=base)
    assert len(listing.json()["items"]) == 2


async def test_no_header_still_executes_normally(idem_client):
    admin = await _seed_user(idem_client.app.state.session_factory, "f1c@x.com")
    r = await idem_client.post(
        "/api/v1/keys", json=KEY_PAYLOAD, headers=_bearer(admin["token"])
    )
    assert r.status_code == 201
    assert r.headers.get("idempotent-replay") is None


async def test_same_key_different_route_does_not_collide(idem_client):
    """Keys are scoped per route template: mint-once on /keys must not shadow
    any other endpoint for the same principal+key."""
    admin = await _seed_user(idem_client.app.state.session_factory, "f1d@x.com")
    headers = {**_bearer(admin["token"]), "Idempotency-Key": "scoped"}
    r = await idem_client.post("/api/v1/keys", json=KEY_PAYLOAD, headers=headers)
    assert r.status_code == 201
    # Same principal+key on a DIFFERENT route template executes independently:
    # the scratch app has no projects router => fresh 404, never the cached
    # mint replayed across routes.
    bad = await idem_client.post("/api/v1/projects", json={}, headers=headers)
    assert bad.status_code == 404
    assert bad.headers.get("idempotent-replay") != "true"
    assert bad.json() != r.json()


def test_migration_0015_defines_table():
    """0015 must exist next to F2's 0013 / E4a's 0014 slot (ls order)."""
    migrations_dir = Path(__file__).parents[1] / "migrations"
    names = sorted(p.name for p in migrations_dir.glob("*.sql"))
    assert any(n.startswith("0015_idempotency") for n in names), names
    sql = (migrations_dir / "0015_idempotency.sql").read_text()
    assert "CREATE TABLE idempotency_keys" in sql
    assert "fingerprint" in sql and "expires_at" in sql


# ------------------------------------------------- F1 fix-loop: JSONB blocker


async def test_reserve_placeholder_is_valid_json(idem_client):
    """F1 fix-loop REGRESSION (VT-1): the reserve INSERT used the literal ''
    for the JSONB ``response_body`` column. asyncpg rejects that
    (InvalidTextRepresentationError), the broad except swallowed it, and the
    middleware failed open on every request — idempotency never engaged in
    production while this fake pool silently accepted ''.

    The pool now validates what the middleware actually inserts against real
    JSON semantics (mirroring asyncpg strict typing), so the old '' literal
    fails right here instead of on live Postgres.
    """
    admin = await _seed_user(idem_client.app.state.session_factory, "f1e@x.com")
    headers = {**_bearer(admin["token"]), "Idempotency-Key": "jsonb-placeholder"}
    r = await idem_client.post("/api/v1/keys", json=KEY_PAYLOAD, headers=headers)
    assert r.status_code == 201, r.text

    pool = idem_client.app.state.idempotency_pool
    reserved = [
        row
        for row in pool.rows.values()
        if row["status_code"] == 0 or row["response_body"] == ""
    ]
    # The mint succeeded => its reserve row was already promoted by _store;
    # any row still sitting at the placeholder proves the sentinel leaked.
    assert reserved == []

    # Direct probe of the storage helper: whatever _reserve writes must parse
    # as JSON ('{}'::jsonb), never the bare '' that Postgres rejects.
    from harness_identity.envelope_middleware import IdempotencyMiddleware

    fresh_pool = FakeAsyncPgPool()
    mw = IdempotencyMiddleware(_noop_asgi)
    ok = await mw._reserve(fresh_pool, "idempotency_keys", "probe|principal|key")
    assert ok is True
    stored = fresh_pool.rows["probe|principal|key"]["response_body"]
    assert isinstance(stored, str) and stored != ""
    assert json.loads(stored) == {}  # valid JSON(B) placeholder


def test_reserve_placeholder_is_bound_jsonb():
    """The reserve INSERT must deliver a JSONB-valid placeholder ('{}') to a
    column typed jsonb. Grep-level tripwire against BOTH failure modes VT-1
    caught: the original bare '' literal (asyncpg rejects it), and an inline
    ``'{}'::jsonb`` literal, which str.format(table=...) would eat as a
    replacement field (IndexError -> fail-open)."""
    src = Path(__file__).parents[1].joinpath(
        "src/harness_identity/envelope_middleware.py"
    ).read_text()
    assert "\"VALUES ($1, 0, '')" not in src  # the exact old blocker
    assert "$2::jsonb" in src  # placeholder bound + explicitly cast to jsonb
    assert '"{}"' in src  # the '{}' placeholder is actually passed as an arg


async def test_live_postgres_accepts_reserve_placeholder():
    """If HARNESS_TEST_PG points at a live Postgres (compose maps 5499),
    prove the reserve/store round-trip against REAL asyncpg + JSONB.
    Skipped otherwise so the offline unit lane stays hermetic."""
    import os

    dsn = os.environ.get("HARNESS_TEST_PG")
    if not dsn:
        pytest.skip("HARNESS_TEST_PG not set; live-PG probe skipped")
    try:
        import asyncpg
    except ImportError:  # pragma: no cover - asyncpg ships in every venv
        pytest.skip("asyncpg unavailable")

    from harness_identity.envelope_middleware import IdempotencyMiddleware

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS f1_probe_idempotency (
                fingerprint TEXT PRIMARY KEY,
                status_code INTEGER NOT NULL DEFAULT 0,
                response_body JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '24 hours'
            )
            """
        )

        class _OneConnPool:
            def acquire(self):
                return _OneConnCtx(conn)

        class _OneConnCtx:
            def __init__(self, c):
                self._c = c

            async def __aenter__(self):
                return self._c

            async def __aexit__(self, *exc):
                return False

        pool = _OneConnPool()
        mw = IdempotencyMiddleware(_noop_asgi)
        fp = f"f1-probe|{datetime.now(UTC).timestamp()}"
        try:
            assert await mw._reserve(pool, "f1_probe_idempotency", fp)
            row = await conn.fetchrow(
                "SELECT status_code, response_body FROM f1_probe_idempotency "
                "WHERE fingerprint = $1",
                fp,
            )
            assert row is not None
            assert int(row["status_code"]) == 0
            # Stock asyncpg hands jsonb back as text ('{}'); '' would have
            # exploded at INSERT time with InvalidTextRepresentationError.
            placeholder = row["response_body"]
            if isinstance(placeholder, str):
                placeholder = json.loads(placeholder)
            assert placeholder == {}
            await mw._store(pool, "f1_probe_idempotency", fp, 201, {"ok": True})
            row = await conn.fetchrow(
                "SELECT status_code, response_body FROM f1_probe_idempotency "
                "WHERE fingerprint = $1",
                fp,
            )
            stored = row["response_body"]
            if isinstance(stored, str):
                stored = json.loads(stored)
            assert int(row["status_code"]) == 201 and stored == {"ok": True}
            cached = await mw._lookup(pool, "f1_probe_idempotency", fp)
            assert cached == (201, {"ok": True})
        finally:
            await conn.execute("DROP TABLE IF EXISTS f1_probe_idempotency")
    finally:
        await conn.close()


async def test_lookup_treats_unstored_reserve_as_miss(idem_client):
    """With the JSONB blocker fixed, reserve rows actually persist — so a
    concurrent duplicate MUST see the status-0 placeholder as a miss (keep
    polling), never replay an invalid status-0 response."""
    mw = IdempotencyMiddleware(_noop_asgi)
    pool = FakeAsyncPgPool()
    fp = "/api/v1/keys|principal|sentinel"

    # Seed exactly what a real PG reserve row looks like pre-store.
    reserved = await mw._reserve(pool, "idempotency_keys", fp)
    assert reserved is True
    # Old _lookup returned (0, {}) here => caller replays status 0. Fixed
    # _lookup must report a miss while the holder is still executing.
    assert await mw._lookup(pool, "idempotency_keys", fp) is None

    await mw._store(pool, "idempotency_keys", fp, 201, {"done": True})
    assert await mw._lookup(pool, "idempotency_keys", fp) == (201, {"done": True})
