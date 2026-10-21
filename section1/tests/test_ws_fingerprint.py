"""M15-W5-S2 (L3) — WS ticket client-binding tests.

The ticket mint stamps fingerprint = sha256(UA + IP)[:16]; the WS upgrade
recomputes it from the upgrading request. Matrix:

  - matching UA+IP            -> accepted (with X-WS-Fingerprint presented)
  - different UA or IP        -> close 4403 TICKET_FINGERPRINT_MISMATCH
  - X-WS-Fingerprint absent   -> close 4403 (REQUIRED since M18-C5 / MAC2-F5;
                                 the warn-and-allow grace path is GONE)
  - X-WS-Fingerprint wrong    -> close 4403
  - UA-only derivation        -> accepted (browser channel; S7 cannot know IP)
  - token/Bearer paths        -> untouched by binding (no ticket to validate)
  - pre-0025 rows (fp NULL)   -> redeemable under the NULL grace path

Dialects: SQLite in-memory-style file DB for the fast suite; live PostgreSQL
(opt-in via HARNESS_TEST_PG, repo convention) proves the migration DDL and the
same behavior over asyncpg.
"""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.app import make_app
from harness_identity.config import Settings
from harness_identity.models import Base

DB_DIR = Path("/tmp/opencode")
DB_FILE = DB_DIR / "ws_fingerprint_test.db"

MINT_UA = "TestAgent/1.0"
MINT_IP = "203.0.113.7"


class WsRejected(AssertionError):
    def __init__(self, code):
        super().__init__(f"websocket rejected with close code {code}")
        self.code = code


class MiniWs:
    """Same single-loop ASGI WS driver as test_ws_auth.py (CI-hang fix), plus
    configurable upgrade-time headers and peer address."""

    def __init__(self, app, target: str, headers: dict[str, str] | None = None,
                 client_addr: tuple[str, int] | None = None):
        parts = urlsplit(target)
        self.scope = {
            "type": "websocket",
            "path": parts.path,
            "query_string": parts.query.encode(),
            # ASGI requires LOWERCASE header names; starlette reads them raw.
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
            "subprotocols": [],
            "server": ("testserver", 80),
            "client": client_addr or ("testclient", 123),
        }
        self.app = app
        self._in: asyncio.Queue = asyncio.Queue()  # app <- client
        self._out: asyncio.Queue = asyncio.Queue()  # app -> client
        self._task: asyncio.Task | None = None
        self.accepted = False
        self.close_code: int | None = None

    async def _from_client(self):
        return await self._in.get()

    async def _to_client(self, message: dict):
        await self._out.put(message)

    async def __aenter__(self) -> "MiniWs":
        self._task = asyncio.ensure_future(self.app(self.scope, self._from_client, self._to_client))
        await self._in.put({"type": "websocket.connect"})  # mandatory ASGI handshake
        first = await asyncio.wait_for(self._out.get(), timeout=5)
        if first["type"] == "websocket.accept":
            self.accepted = True
        elif first["type"] == "websocket.close":
            self.close_code = first.get("code", 1000)
        return self

    async def recv_json(self, timeout: float = 5):
        while True:
            msg = await asyncio.wait_for(self._out.get(), timeout=timeout)
            if msg["type"] == "websocket.send":
                return json.loads(msg["text"])
            if msg["type"] == "websocket.close":
                self.close_code = msg.get("code", 1000)
                raise WsRejected(self.close_code)

    async def disconnect(self, code: int = 1000):
        await self._in.put({"type": "websocket.disconnect", "code": code})

    async def __aexit__(self, *exc) -> None:
        if self.accepted and self.close_code is None:
            await self._in.put({"type": "websocket.disconnect", "code": 1000})
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


def _expect_reject(ws: MiniWs, code: int):
    assert not ws.accepted, "expected rejection but socket was accepted"
    assert ws.close_code == code, f"expected close {code}, got {ws.close_code}"


def expected_fp(ua: str, ip: str) -> str:
    """Mirror of harness_identity.ephemeral.compute_client_fingerprint."""
    return hashlib.sha256(f"{ua}{ip}".encode()).hexdigest()[:16]


@pytest.fixture
async def db():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    if DB_FILE.exists():
        DB_FILE.unlink()

    sync_engine = create_engine(f"sqlite:///{DB_FILE}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    async_eng = create_async_engine(f"sqlite+aiosqlite:///{DB_FILE}")
    try:
        yield async_sessionmaker(async_eng, expire_on_commit=False)
    finally:
        # Same aiosqlite non-daemon-thread hazard as test_ownership.py.
        await async_eng.dispose()
        if DB_FILE.exists():
            DB_FILE.unlink()


def _seed_sync(with_fingerprint_column: bool = True):
    con = __import__("sqlite3").connect(DB_FILE)
    later = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    con.executescript(
        f"""
        INSERT INTO users (id, email, name, is_active) VALUES ('u1','a@x.com','A',1);
        INSERT INTO teams (id, name) VALUES ('t1','T');
        INSERT INTO projects (id, team_id, name) VALUES ('proj-1','t1','demo');
        INSERT INTO team_members (id, team_id, user_id, display_name, member_type, role)
            VALUES ('m1','t1','u1','A','HUMAN','ADMIN');
        INSERT INTO auth_sessions (token_hash, user_id, team_id, expires_at)
            VALUES ('{hashlib.sha256(b"goodtok").hexdigest()}','u1','t1','{later}');
        """
    )
    con.commit()
    con.close()
    # Ephemeral-state tables (migration 0005 + 0025) — tests own their SQLite schema.
    fp_col = ",\n            fingerprint VARCHAR(32)" if with_fingerprint_column else ""
    con = __import__("sqlite3").connect(DB_FILE)
    con.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS ws_tickets (
            token_hash VARCHAR(64) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            team_id VARCHAR(36) NOT NULL,
            role VARCHAR(20) NOT NULL,
            project_id VARCHAR(36) NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL{fp_col});
        """
    )
    con.commit()
    con.close()


def _mk(db):
    app = make_app(db, Settings(master_key=b"test-master-key-0123456789abcdef", cors_origins=[]))
    return app


def _http(app, **client_kwargs) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t", **client_kwargs
    )


async def _issue_ticket(app, project_id="proj-1", token="goodtok"):
    raise NotImplementedError  # superseded by _mint_ticket below


async def _mint_ticket(app, project_id="proj-1", token="goodtok"):
    async with AsyncClient(
        transport=ASGITransport(app=app, client=(MINT_IP, 50000)), base_url="http://t"
    ) as c:
        return await c.post(
            "/api/v1/ws-ticket",
            json={"projectId": project_id},
            headers={"Authorization": f"Bearer {token}", "User-Agent": MINT_UA},
        )


# ================================================================
# Pure helper
# ================================================================


def test_fingerprint_shape_and_sensitivity(db):
    from harness_identity.ephemeral import FINGERPRINT_HEX_LEN, compute_client_fingerprint

    fp = compute_client_fingerprint(MINT_UA, MINT_IP)
    assert len(fp) == FINGERPRINT_HEX_LEN == 16
    assert all(c in "0123456789abcdef" for c in fp)
    # Either input changing must move the hash...
    assert fp != compute_client_fingerprint("Other/2.0", MINT_IP)
    assert fp != compute_client_fingerprint(MINT_UA, "198.51.100.9")
    # ...and the pure function agrees with the spec arithmetic sha256(UA+IP)[:16].
    assert fp == hashlib.sha256(f"{MINT_UA}{MINT_IP}".encode()).hexdigest()[:16]


# ================================================================
# Mint binds the fingerprint
# ================================================================


async def test_mint_binds_ua_ip_fingerprint(db):
    from harness_identity.ephemeral import compute_client_fingerprint

    app = _mk(db)
    _seed_sync()
    r = await _mint_ticket(app)
    assert r.status_code == 200
    body = r.json()
    assert body["ticket"] and body["expiresInSeconds"] > 0

    con = __import__("sqlite3").connect(DB_FILE)
    row = con.execute(
        "SELECT fingerprint FROM ws_tickets WHERE project_id = 'proj-1'"
    ).fetchone()
    con.close()
    assert row is not None
    assert row[0] == compute_client_fingerprint(MINT_UA, MINT_IP)


# ================================================================
# Upgrade validation matrix (ticket path)
# ================================================================


async def test_upgrade_same_client_accepted(db):
    app = _mk(db)
    _seed_sync()
    mint_body = (await _mint_ticket(app)).json()
    ticket = mint_body["ticket"]
    # M18-C5 (F5): mint echoes the stored binding; an honest non-browser
    # client presents exactly that value back at upgrade.
    assert mint_body["fingerprint"] == expected_fp(MINT_UA, MINT_IP)
    # Same UA + same peer IP as at mint time, fingerprint presented.
    async with MiniWs(
        app,
        f"/api/v1/ws/proj-1?ticket={ticket}",
        headers={
            "User-Agent": MINT_UA,
            "X-WS-Fingerprint": mint_body["fingerprint"],
        },
        client_addr=(MINT_IP, 51000),
    ) as ws:
        assert ws.accepted


async def test_browser_ua_only_derivation_accepted(db):
    """M18-C5 (F5): browsers cannot know their own IP, so S7 ships the UA-only
    derivation sha256(UA)[:16] as the fingerprint param. Presentation proves
    the upgrading process computed the binding input; the authoritative
    stored-vs-recomputed UA+IP check still runs server-side."""
    app = _mk(db)
    _seed_sync()
    ticket = (await _mint_ticket(app)).json()["ticket"]
    ua_only = hashlib.sha256(MINT_UA.encode()).hexdigest()[:16]
    async with MiniWs(
        app,
        f"/api/v1/ws/proj-1?ticket={ticket}&fingerprint={ua_only}",
        headers={"User-Agent": MINT_UA},
        client_addr=(MINT_IP, 51001),
    ) as ws:
        assert ws.accepted


@pytest.mark.parametrize("which", ["ua", "ip"])
async def test_upgrade_mismatched_client_rejected_4403(db, which):
    """A ticket replayed from a different host (IP) or client (UA) dies with
    4403 TICKET_FINGERPRINT_MISMATCH before any project access check runs."""
    app = _mk(db)
    _seed_sync()
    ticket = (await _mint_ticket(app)).json()["ticket"]
    headers = {"User-Agent": MINT_UA} if which == "ip" else {"User-Agent": "Impostor/9.9"}
    addr = (MINT_IP, 52000) if which == "ua" else ("198.51.100.66", 52000)
    async with MiniWs(app, f"/api/v1/ws/proj-1?ticket={ticket}",
                      headers=headers, client_addr=addr) as ws:
        _expect_reject(ws, 4403)


async def test_mismatch_on_foreign_project_still_answers_4401(db):
    """Ordering law (M13-G5a preserved): redemption is project-bound, so a
    stolen ticket pointed at ANOTHER team's project dies 4401 at redemption —
    before fingerprint validation would ever run. Binding adds a second,
    independent wall for same-project replay; it does not reorder auth."""
    _seed_sync()
    con = __import__("sqlite3").connect(DB_FILE)
    con.executescript(
        """
        INSERT INTO teams (id, name) VALUES ('t2','OTHER');
        INSERT INTO projects (id, team_id, name) VALUES ('proj-other','t2','secret');
        """
    )
    con.commit()
    con.close()
    app = _mk(db)
    ticket = (await _mint_ticket(app)).json()["ticket"]
    async with MiniWs(app, "/api/v1/ws/proj-other?ticket=" + ticket,
                      headers={"User-Agent": "Other/0"}, client_addr=("192.0.2.9", 80)) as ws:
        _expect_reject(ws, 4401)


# ================================================================
# X-WS-Fingerprint header semantics (optional this release)
# ================================================================


async def test_absent_header_rejected_4403(db):
    """M18-C5 (MAC2-F5): presentation is REQUIRED — the old warn-and-allow
    grace path let a replaying attacker evaporate the header half of the
    binding by omitting it. Absent header AND absent browser param now die
    with 4403 even though UA and peer IP still match mint exactly."""
    app = _mk(db)
    _seed_sync()
    ticket = (await _mint_ticket(app)).json()["ticket"]
    async with MiniWs(
        app,
        f"/api/v1/ws/proj-1?ticket={ticket}",
        headers={"User-Agent": MINT_UA},  # no X-WS-Fingerprint, no ?fingerprint=
        client_addr=(MINT_IP, 53000),
    ) as ws:
        _expect_reject(ws, 4403)


async def test_wrong_header_rejected_4403(db):
    app = _mk(db)
    _seed_sync()
    ticket = (await _mint_ticket(app)).json()["ticket"]
    async with MiniWs(
        app,
        f"/api/v1/ws/proj-1?ticket={ticket}",
        headers={"User-Agent": MINT_UA, "X-WS-Fingerprint": "0123456789abcdef"},
        client_addr=(MINT_IP, 54000),
    ) as ws:
        _expect_reject(ws, 4403)


async def test_correct_header_accepted(db):
    app = _mk(db)
    _seed_sync()
    ticket = (await _mint_ticket(app)).json()["ticket"]
    async with MiniWs(
        app,
        f"/api/v1/ws/proj-1?ticket={ticket}",
        headers={
            "User-Agent": MINT_UA,
            "X-WS-Fingerprint": expected_fp(MINT_UA, MINT_IP),
        },
        client_addr=(MINT_IP, 55000),
    ) as ws:
        assert ws.accepted


async def test_query_param_channel_is_the_browser_path(db):
    """Browsers cannot set custom headers on new WebSocket(url), so the client
    ships the same value as the `fingerprint` query param. Correct param ->
    accepted; wrong param -> 4403, exactly like the header."""
    app = _mk(db)
    _seed_sync()
    ticket = (await _mint_ticket(app)).json()["ticket"]
    async with MiniWs(
        app,
        f"/api/v1/ws/proj-1?ticket={ticket}&fingerprint={expected_fp(MINT_UA, MINT_IP)}",
        headers={"User-Agent": MINT_UA},
        client_addr=(MINT_IP, 57000),
    ) as ws:
        assert ws.accepted

    ticket2 = (await _mint_ticket(app)).json()["ticket"]
    async with MiniWs(
        app,
        f"/api/v1/ws/proj-1?ticket={ticket2}&fingerprint={'0' * 16}",
        headers={"User-Agent": MINT_UA},
        client_addr=(MINT_IP, 57001),
    ) as ws:
        _expect_reject(ws, 4403)


async def test_header_cannot_override_server_side_binding(db):
    """The header is advisory evidence; the stored fingerprint is the truth. A
    correct header cannot smuggle an upgrade whose UA/IP no longer match."""
    app = _mk(db)
    _seed_sync()
    ticket = (await _mint_ticket(app)).json()["ticket"]
    async with MiniWs(
        app,
        f"/api/v1/ws/proj-1?ticket={ticket}",
        headers={
            "User-Agent": MINT_UA,
            "X-WS-Fingerprint": expected_fp(MINT_UA, MINT_IP),  # "right" header…
        },
        client_addr=("198.51.100.77", 56000),  # …from the wrong host
    ) as ws:
        _expect_reject(ws, 4403)


# ================================================================
# Backward compatibility: non-ticket paths and pre-0025 rows
# ================================================================


async def test_token_and_bearer_paths_unaffected(db):
    """No ticket -> nothing to bind; both legacy auth paths keep working."""
    app = _mk(db)
    _seed_sync()
    async with MiniWs(app, "/api/v1/ws/proj-1?token=goodtok") as ws:
        assert ws.accepted
    async with MiniWs(app, "/api/v1/ws/proj-1",
                      headers={"Authorization": "Bearer goodtok"}) as ws:
        assert ws.accepted


async def test_pre_migration_row_without_fingerprint_still_redeems(db):
    """Grace path for rows minted before migration 0025 (fingerprint NULL)."""
    from harness_contracts import Principal
    from harness_contracts.roles import Role

    from harness_identity.ephemeral import RedeemedTicket, redeem_ws_ticket

    _seed_sync()
    con = __import__("sqlite3").connect(DB_FILE)
    h = hashlib.sha256(b"legacy-ticket").hexdigest()
    later = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
    con.execute(
        "INSERT INTO ws_tickets (token_hash, user_id, team_id, role, project_id,"
        " expires_at, created_at, fingerprint)"
        " VALUES (?, 'u1', 't1', 'ADMIN', 'proj-1', ?, ?, NULL)",
        (h, later, datetime.now(UTC).isoformat()),
    )
    con.commit()
    con.close()

    redeemed = await redeem_ws_ticket(db, "legacy-ticket", "proj-1")
    assert isinstance(redeemed, RedeemedTicket)
    assert redeemed.principal == Principal(userId="u1", teamId="t1", role=Role.ADMIN)
    assert redeemed.fingerprint is None

    # And through the route: bound=None skips the stored-fp comparison; the
    # absent header takes the grace path -> accepted.
    app = _mk(db)
    con = __import__("sqlite3").connect(DB_FILE)
    con.execute(
        "INSERT INTO ws_tickets (token_hash, user_id, team_id, role, project_id,"
        " expires_at, created_at, fingerprint)"
        " VALUES (?, 'u1', 't1', 'ADMIN', 'proj-1', ?, ?, NULL)",
        (hashlib.sha256(b"legacy-2").hexdigest(), later, datetime.now(UTC).isoformat()),
    )
    con.commit()
    con.close()
    async with MiniWs(app, "/api/v1/ws/proj-1?ticket=legacy-2") as ws:
        assert ws.accepted


async def test_direct_mint_without_client_context_defaults_empty_fp(db):
    """mint_ws_ticket's default UA/IP stay "" so internal callers are stable;
    such tickets simply never match a real client (fail-closed binding)."""
    from harness_contracts import Principal
    from harness_contracts.roles import Role

    from harness_identity.ephemeral import (
        compute_client_fingerprint,
        mint_ws_ticket,
        redeem_ws_ticket,
    )

    _seed_sync()
    principal = Principal(userId="u1", teamId="t1", role=Role.ADMIN)
    ticket, ttl = await mint_ws_ticket(db, principal, "proj-1")
    assert ttl > 0
    redeemed = await redeem_ws_ticket(db, ticket, "proj-1")
    assert redeemed is not None
    assert redeemed.fingerprint == compute_client_fingerprint("", "")
    app = _mk(db)
    # No client can reproduce the ""+"" fingerprint over a real socket unless it
    # truly has neither UA nor peer address — the empty case stays fail-closed.
    async with MiniWs(app, "/api/v1/ws/proj-1?ticket=never-minted") as ws:
        _expect_reject(ws, 4401)


# ================================================================
# Dual dialect: the migration chain on LIVE Postgres (opt-in).
# ================================================================


_pg_reason = "HARNESS_TEST_PG not set; live-PG fingerprint probe skipped"


def _pg_dsn() -> str:
    raw = __import__("os").environ.get("HARNESS_TEST_PG")
    if not raw or raw.strip().lower() in {"1", "true", "yes", "on"}:
        return __import__("os").environ.get("HARNESS_TEST_PG_DSN") or "postgresql://harness:harness@localhost:5499/harness"
    dsn = raw
    if "://" not in dsn:
        dsn = f"postgresql://{dsn}"
    return dsn


def _migration(name: str) -> str:
    root = Path(__file__).resolve().parent.parent / "migrations"
    return (root / name).read_text()


def _split_sql(sql_text: str) -> list[str]:
    lines = [ln for ln in sql_text.splitlines() if not ln.strip().startswith("--")]
    out: list[str] = []
    for stmt in "\n".join(lines).split(";"):
        s = stmt.strip()
        if s:
            out.append(s)
    return out


@pytest.mark.skipif(not __import__("os").environ.get("HARNESS_TEST_PG"), reason=_pg_reason)
class TestLivePostgresFingerprintMigration:
    async def test_migration_adds_nullable_fingerprint_and_route_binds_it(self):
        import asyncpg

        dbname = f"harness_s2_{__import__('uuid').uuid4().hex[:10]}"
        admin = await asyncpg.connect(_pg_dsn())
        await admin.execute(f'CREATE DATABASE "{dbname}"')
        try:
            conn = await asyncpg.connect(dsn=f"{_pg_dsn().rsplit('/', 1)[0]}/{dbname}")
            try:
                for stmt in _split_sql(_migration("0001_initial.sql")):
                    await conn.execute(stmt)
                for stmt in _split_sql(_migration("0005_ephemeral_state.sql")):
                    await conn.execute(stmt)
                for stmt in _split_sql(_migration("0025_ws_fingerprint.sql")):
                    await conn.execute(stmt)

                cols = {
                    r["column_name"]: r
                    for r in await conn.fetch(
                        "SELECT column_name, is_nullable, data_type, character_maximum_length"
                        " FROM information_schema.columns WHERE table_name = 'ws_tickets'"
                    )
                }
                assert "fingerprint" in cols
                assert cols["fingerprint"]["is_nullable"] == "YES"
                assert int(cols["fingerprint"]["character_maximum_length"]) == 32

                # Idempotent: re-running the migration is a no-op.
                for stmt in _split_sql(_migration("0025_ws_fingerprint.sql")):
                    await conn.execute(stmt)

                now_iso = datetime.now(UTC).isoformat()
                fp = hashlib.sha256(f"{MINT_UA}{MINT_IP}".encode()).hexdigest()[:16]
                await conn.execute(
                    "INSERT INTO users (id, email, name, is_active)"
                    " VALUES ('u1','a@x.com','A', TRUE)"
                )
                await conn.execute("INSERT INTO teams (id, name) VALUES ('t1','T')")
                await conn.execute(
                    "INSERT INTO projects (id, team_id, name) VALUES ('proj-1','t1','demo')"
                )
                await conn.execute(
                    "INSERT INTO team_members (id, team_id, user_id, display_name,"
                    " member_type, role) VALUES ('m1','t1','u1','A','HUMAN','ADMIN')"
                )
                await conn.execute(
                    "INSERT INTO ws_tickets (token_hash, user_id, team_id, role,"
                    " project_id, expires_at, created_at, fingerprint)"
                    " VALUES ($1, 'u1', 't1', 'ADMIN', 'proj-1', $2, $3, $4)",
                    hashlib.sha256(b"pg-tick").hexdigest(),
                    (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
                    now_iso,
                    fp,
                )
                row = await conn.fetchrow(
                    "DELETE FROM ws_tickets WHERE token_hash = $1 AND project_id = $2"
                    " AND expires_at > $3 RETURNING user_id, fingerprint",
                    hashlib.sha256(b"pg-tick").hexdigest(),
                    "proj-1",
                    now_iso,
                )
                assert row["user_id"] == "u1" and row["fingerprint"] == fp
            finally:
                await conn.close()
        finally:
            await admin.execute(f'DROP DATABASE "{dbname}"')
            await admin.close()


async def test_ticket_minted_and_redeemed_through_a_trusted_proxy(db):
    """Part 54 (session 12): behind a proxy in trusted_proxy_cidrs that adds X-Forwarded-For (the
    deploy Caddyfile), the mint binds the forwarded client IP; the upgrade must derive its IP the
    same way. It used the raw peer (the proxy) and refused every such socket with 4403. A ticket
    replayed with another client's XFF through the same proxy is still refused."""
    app = _mk(db)
    _seed_sync()
    proxy, client_ip = "10.0.0.5", "203.0.113.20"
    async with AsyncClient(
        transport=ASGITransport(app=app, client=(proxy, 50000)), base_url="http://t"
    ) as c:
        minted = (await c.post(
            "/api/v1/ws-ticket", json={"projectId": "proj-1"},
            headers={"Authorization": "Bearer goodtok", "User-Agent": MINT_UA,
                     "X-Forwarded-For": client_ip},
        )).json()
    assert minted["fingerprint"] == expected_fp(MINT_UA, client_ip)
    ua_only = hashlib.sha256(MINT_UA.encode()).hexdigest()[:16]
    async with MiniWs(
        app, f"/api/v1/ws/proj-1?ticket={minted['ticket']}&fingerprint={ua_only}",
        headers={"User-Agent": MINT_UA, "X-Forwarded-For": client_ip},
        client_addr=(proxy, 51000),
    ) as ws:
        assert ws.accepted

    async with AsyncClient(
        transport=ASGITransport(app=app, client=(proxy, 50001)), base_url="http://t"
    ) as c:
        second = (await c.post(
            "/api/v1/ws-ticket", json={"projectId": "proj-1"},
            headers={"Authorization": "Bearer goodtok", "User-Agent": MINT_UA,
                     "X-Forwarded-For": client_ip},
        )).json()
    async with MiniWs(
        app, f"/api/v1/ws/proj-1?ticket={second['ticket']}&fingerprint={ua_only}",
        headers={"User-Agent": MINT_UA, "X-Forwarded-For": "198.51.100.66"},
        client_addr=(proxy, 51001),
    ) as ws:
        _expect_reject(ws, 4403)
