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
DB_FILE = DB_DIR / "ws_auth_test.db"

# M15-W5-S2: tickets are client-bound, so tests mint and upgrade as one fixed
# synthetic client (same UA + peer address on both legs).
MINT_UA = "TestAgent/1.0"
MINT_IP = "203.0.113.7"


class WsRejected(AssertionError):
    def __init__(self, code):
        super().__init__(f"websocket rejected with close code {code}")
        self.code = code


class MiniWs:
    """Drives a raw ASGI websocket session on the CURRENT asyncio loop.

    Replaces starlette.testclient's threaded portal here: the portal ran app
    code on a different event loop than the test's DB engine, and aiosqlite
    pooled connections bound to the dead fixture loop wedged the handler
    forever (the CI hang). Single-loop = deterministic.
    """

    def __init__(self, app, target: str, headers: dict[str, str] | None = None,
                 client_addr: tuple[str, int] | None = None):
        self.headers = headers or {}
        parts = urlsplit(target)
        self.scope = {
            "type": "websocket",
            "path": parts.path,
            "query_string": parts.query.encode(),
            # M15-W5-S2: ASGI requires LOWERCASE header names; starlette reads
            # them raw, so "User-Agent" would silently read back as "".
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


def _seed_sync():
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
    # Ephemeral-state tables (migration 0005) — tests own their SQLite schema.
    con = __import__("sqlite3").connect(DB_FILE)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS ws_tickets (
            token_hash VARCHAR(64) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            team_id VARCHAR(36) NOT NULL,
            role VARCHAR(20) NOT NULL,
            project_id VARCHAR(36) NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
            fingerprint VARCHAR(32));
        """
    )
    con.commit()
    con.close()


def _mk(db):
    app = make_app(db, Settings(master_key=b"test-master-key-0123456789abcdef", cors_origins=[]))
    return app


def _http(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


# ================================================================
# H8/F3 guards, single-loop edition
# ================================================================


async def test_ws_rejects_unauthenticated_client(db):
    app = _mk(db)
    async with MiniWs(app, "/api/v1/ws/proj-1") as ws:
        _expect_reject(ws, 4401)


async def test_ws_rejects_bad_token(db):
    app = _mk(db)
    async with MiniWs(app, "/api/v1/ws/proj-1?token=bogus") as ws:
        _expect_reject(ws, 4401)


async def test_ws_accepts_valid_token(db):
    _seed_sync()
    app = _mk(db)
    async with MiniWs(app, "/api/v1/ws/proj-1?token=goodtok") as ws:
        assert ws.accepted


async def test_ws_rejects_foreign_team_project(db):
    """F3 regression guard: valid token, but project belongs to another team."""
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
    async with MiniWs(app, "/api/v1/ws/proj-other?token=goodtok") as ws:
        # M13-G5a no-oracle law: foreign-project upgrades close 4404 — same as
        # an unknown project; 4403 is reserved for auth failures.
        _expect_reject(ws, 4404)


# ================================================================
# H8: one-time WS tickets replace query-string tokens
# ================================================================


async def _issue_ticket(app, project_id="proj-1", token="goodtok"):
    """Mint as a FIXED synthetic client (UA + peer address below); upgrades in
    this file that expect acceptance must present the same values, since
    M15-W5-S2 binds every ticket to the minting client."""
    async with AsyncClient(
        transport=ASGITransport(app=app, client=(MINT_IP, 50000)), base_url="http://t"
    ) as c:
        return await c.post(
            "/api/v1/ws-ticket",
            json={"projectId": project_id},
            headers={"Authorization": f"Bearer {token}", "User-Agent": MINT_UA},
        )


async def test_ws_ticket_requires_auth(db):
    app = _mk(db)
    _seed_sync()
    r = await _issue_ticket(app, token=None)  # type: ignore[arg-type]
    assert r.status_code == 401


async def test_ws_ticket_rejects_foreign_project(db):
    """F3 guard extended to tickets: cannot mint a ticket for another team's project."""
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
    r = await _issue_ticket(app, project_id="proj-other")
    # M13-G5a no-oracle law: minting for another team's project answers the
    # same NOT_FOUND an unknown project would — never a 403 existence oracle.
    assert r.status_code == 404
    assert r.json()["detail"]["error"]["code"] == "NOT_FOUND"


async def test_ws_ticket_happy_path(db):
    app = _mk(db)
    _seed_sync()
    body = (await _issue_ticket(app)).json()
    assert body["ticket"]
    assert body["expiresInSeconds"] > 0
    # M18-C5 (F5): fingerprint presentation required at upgrade; the mint
    # response echoes the stored binding for exactly this purpose.
    assert body["fingerprint"] == hashlib.sha256(
        f"{MINT_UA}{MINT_IP}".encode()
    ).hexdigest()[:16]
    async with MiniWs(app, f"/api/v1/ws/proj-1?ticket={body['ticket']}",
                      headers={"User-Agent": MINT_UA,
                               "X-WS-Fingerprint": body["fingerprint"]},
                      client_addr=(MINT_IP, 123)) as ws:
        assert ws.accepted


async def test_ws_ticket_is_single_use(db):
    app = _mk(db)
    _seed_sync()
    minted = (await _issue_ticket(app)).json()
    ticket = minted["ticket"]

    async with MiniWs(app, f"/api/v1/ws/proj-1?ticket={ticket}",
                      headers={"User-Agent": MINT_UA,
                               "X-WS-Fingerprint": minted["fingerprint"]},
                      client_addr=(MINT_IP, 123)) as ws:
        assert ws.accepted
    # ...and the replayed ticket MUST be rejected.
    async with MiniWs(app, f"/api/v1/ws/proj-1?ticket={ticket}",
                      headers={"User-Agent": MINT_UA,
                               "X-WS-Fingerprint": minted["fingerprint"]},
                      client_addr=(MINT_IP, 123)) as ws:
        _expect_reject(ws, 4401)


async def test_ws_ticket_bound_to_project(db):
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
    ticket = (await _issue_ticket(app, project_id="proj-1")).json()["ticket"]

    # Redemption pops the ticket; a project mismatch makes it return None, which
    # surfaces as 4401 (invalid credentials) — NOT 4403 (which is reserved for
    # valid-token-but-foreign-project).
    async with MiniWs(app, f"/api/v1/ws/proj-other?ticket={ticket}") as ws:
        _expect_reject(ws, 4401)


async def test_ws_ticket_unknown_rejected(db):
    app = _mk(db)
    _seed_sync()
    async with MiniWs(app, "/api/v1/ws/proj-1?ticket=bogus") as ws:
        _expect_reject(ws, 4401)


async def test_ws_ticket_expired_rejected(db):
    app = _mk(db)
    _seed_sync()
    ticket = (await _issue_ticket(app)).json()["ticket"]
    import sqlite3 as _sq

    con = _sq.connect(DB_FILE)
    con.execute(
        "UPDATE ws_tickets SET expires_at = ?",
        ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),),
    )
    con.commit()
    con.close()

    async with MiniWs(app, f"/api/v1/ws/proj-1?ticket={ticket}") as ws:
        _expect_reject(ws, 4401)


# ================================================================
# Cross-process tickets: DB-backed store shared by two app instances.
# ================================================================


def _shared_factory():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    sync_engine = create_engine(f"sqlite:///{DB_FILE}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    eng = create_async_engine(f"sqlite+aiosqlite:///{DB_FILE}")
    return async_sessionmaker(eng, expire_on_commit=False)


async def test_ws_tickets_survive_across_instances(db):
    """Mint on instance A's factory, redeem through instance B's factory."""
    from harness_contracts import Principal

    from harness_identity.ephemeral import mint_ws_ticket, redeem_ws_ticket

    _seed_sync()
    factory_b = _shared_factory()
    principal = Principal(userId="u1", teamId="t1", role="admin")
    ticket, ttl = await mint_ws_ticket(factory_b, principal, "proj-1")
    assert ttl > 0

    # Redeemed via a *different* process-equivalent (instance A = `db` fixture).
    redeemed = await redeem_ws_ticket(db, ticket, "proj-1")
    assert redeemed is not None and redeemed.principal.userId == "u1"
    assert await redeem_ws_ticket(db, ticket, "proj-1") is None  # single use
    assert await redeem_ws_ticket(db, ticket + "x", "proj-1") is None

    await factory_b.kw["bind"].dispose()


async def test_token_path_still_works_during_transition(db):
    app = _mk(db)
    _seed_sync()
    async with MiniWs(app, "/api/v1/ws/proj-1?token=goodtok") as ws:
        assert ws.accepted


# ================================================================
# CI-hang regression guard: a client that connects and closes with no
# events flowing must NOT wedge the server handler (it used to park on
# queue.get() forever, hanging TestClient teardown on Linux).
# ================================================================


async def test_silent_client_disconnect_does_not_wedge_handler(db):
    app = _mk(db)
    _seed_sync()
    for _ in range(3):
        async with MiniWs(app, "/api/v1/ws/proj-1?token=goodtok") as ws:
            assert ws.accepted
            await ws.disconnect()  # open and immediately drop; server unwinds cleanly

    # The connection is reusable afterwards: handler released the subscription
    # instead of leaking it, and a fresh client gets a normal session.
    async with MiniWs(app, "/api/v1/ws/proj-1?token=goodtok") as ws:
        assert ws.accepted
