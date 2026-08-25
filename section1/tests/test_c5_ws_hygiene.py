"""M18-C5 (MAC2-F5 / MAC2-F7) — WS ticket fingerprint enforcement + token
deprecation regression tests.

FINDING F5 (m17-findings-mac2.md): the absent-header grace path let a
replaying attacker evaporate the header half of the ticket binding by simply
omitting X-WS-Fingerprint, reducing the whole control to the spoofable
server-side UA+IP recomputation. Fixed: when mint recorded a fingerprint
(every HTTP mint does), presentation is REQUIRED — absent -> 4403. The only
remaining grace path is pre-0025 rows whose stored fingerprint is NULL.

FINDING F7 (same file): a LONG-LIVED session credential was accepted via the
?token= query param on the WS upgrade — exactly what H8 forbids (query
strings land in access/proxy logs and browser history). Fixed in two steps
per backward-compat law: this release logs a loud deprecation warning on
every ?token= upgrade; next release it is removed. The ws-ticket flow is the
routed-to replacement.

Uses the same single-loop MiniWs ASGI driver as test_ws_auth.py (starlette
TestClient wedges app code on a foreign loop — repo law).
"""

import asyncio
import hashlib
import logging
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
DB_FILE = DB_DIR / "c5_ws_hygiene_test.db"

MINT_UA = "C5Agent/1.0"
MINT_IP = "203.0.113.7"


def expected_fp(ua: str, ip: str) -> str:
    """Mirror of harness_identity.ephemeral.compute_client_fingerprint."""
    return hashlib.sha256(f"{ua}{ip}".encode()).hexdigest()[:16]


class MiniWs:
    """Single-loop raw-ASGI websocket driver (see test_ws_auth.py)."""

    def __init__(self, app, target: str, headers=None, client_addr=None):
        self.headers = headers or {}
        parts = urlsplit(target)
        self.scope = {
            "type": "websocket",
            "path": parts.path,
            "query_string": parts.query.encode(),
            "headers": [
                (k.lower().encode(), v.encode()) for k, v in (self.headers).items()
            ],
            "subprotocols": [],
            "server": ("testserver", 80),
            "client": client_addr or ("testclient", 123),
        }
        self.app = app
        self._in: asyncio.Queue = asyncio.Queue()
        self._out: asyncio.Queue = asyncio.Queue()
        self._task = None
        self.accepted = False
        self.close_code = None

    async def _from_client(self):
        return await self._in.get()

    async def _to_client(self, message):
        await self._out.put(message)

    async def __aenter__(self):
        self._task = asyncio.ensure_future(self.app(self.scope, self._from_client, self._to_client))
        await self._in.put({"type": "websocket.connect"})
        first = await asyncio.wait_for(self._out.get(), timeout=5)
        if first["type"] == "websocket.accept":
            self.accepted = True
        elif first["type"] == "websocket.close":
            self.close_code = first.get("code", 1000)
        return self

    async def __aexit__(self, *exc):
        if self.accepted and self.close_code is None:
            await self._in.put({"type": "websocket.disconnect", "code": 1000})
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


def _expect_reject(ws, code):
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
# F5 — mint echoes the stored fingerprint
# ================================================================


async def test_mint_response_echoes_stored_fingerprint(db):
    """Non-browser callers cannot recompute sha256(UA+IP) themselves (they do
    not know how the server normalized their IP), so mint hands them the
    authoritative value to present at upgrade."""
    app = _mk(db)
    _seed_sync()
    r = await _mint_ticket(app)
    assert r.status_code == 200
    body = r.json()
    assert body["fingerprint"] == expected_fp(MINT_UA, MINT_IP)


# ================================================================
# F5 — presentation matrix at upgrade
# ================================================================


async def test_upgrade_without_any_fingerprint_rejected_4403(db):
    """THE F5 fix: even with perfect UA+IP match, omitting the fingerprint
    dies. The grace path that made binding opt-in-by-header is gone."""
    app = _mk(db)
    _seed_sync()
    ticket = (await _mint_ticket(app)).json()["ticket"]
    async with MiniWs(
        app,
        f"/api/v1/ws/proj-1?ticket={ticket}",
        headers={"User-Agent": MINT_UA},
        client_addr=(MINT_IP, 61000),
    ) as ws:
        _expect_reject(ws, 4403)


async def test_upgrade_with_correct_header_accepted(db):
    app = _mk(db)
    _seed_sync()
    ticket = (await _mint_ticket(app)).json()["ticket"]
    async with MiniWs(
        app,
        f"/api/v1/ws/proj-1?ticket={ticket}",
        headers={"User-Agent": MINT_UA, "X-WS-Fingerprint": expected_fp(MINT_UA, MINT_IP)},
        client_addr=(MINT_IP, 61001),
    ) as ws:
        assert ws.accepted


async def test_browser_ua_only_param_accepted_full_form_wrong_rejected(db):
    """S7's browser channel presents the UA-only derivation as the param.
    Full-form presented value also accepted; garbage still 4403."""
    app = _mk(db)
    _seed_sync()
    ua_only = hashlib.sha256(MINT_UA.encode()).hexdigest()[:16]

    ticket = (await _mint_ticket(app)).json()["ticket"]
    async with MiniWs(
        app,
        f"/api/v1/ws/proj-1?ticket={ticket}&fingerprint={ua_only}",
        headers={"User-Agent": MINT_UA},
        client_addr=(MINT_IP, 61002),
    ) as ws:
        assert ws.accepted

    ticket = (await _mint_ticket(app)).json()["ticket"]
    async with MiniWs(
        app,
        f"/api/v1/ws/proj-1?ticket={ticket}&fingerprint={expected_fp(MINT_UA, MINT_IP)}",
        headers={"User-Agent": MINT_UA},
        client_addr=(MINT_IP, 61003),
    ) as ws:
        assert ws.accepted

    ticket = (await _mint_ticket(app)).json()["ticket"]
    wrong_ua_only = hashlib.sha256(b"Impostor/9").hexdigest()[:16]
    async with MiniWs(
        app,
        f"/api/v1/ws/proj-1?ticket={ticket}&fingerprint={wrong_ua_only}",
        headers={"User-Agent": MINT_UA},
        client_addr=(MINT_IP, 61004),
    ) as ws:
        _expect_reject(ws, 4403)


async def test_forged_header_from_wrong_host_still_dies_on_binding(db):
    """Presentation alone proves nothing: an attacker who guesses/presents a
    'valid-shaped' fingerprint from another host still fails the AUTHORITATIVE
    check (stored mint fp vs recomputed UA+IP of THIS request)."""
    app = _mk(db)
    _seed_sync()
    ticket = (await _mint_ticket(app)).json()["ticket"]
    async with MiniWs(
        app,
        f"/api/v1/ws/proj-1?ticket={ticket}",
        # Attacker presents THEIR OWN UA-only derivation...
        headers={
            "User-Agent": MINT_UA,
            "X-WS-Fingerprint": expected_fp(MINT_UA, ""),
        },
        client_addr=("198.51.100.99", 61005),  # ...but from the WRONG host
    ) as ws:
        # Their derivation is not valid for this request's UA+host, and the
        # stored-vs-recomputed comparison kills the replay regardless.
        _expect_reject(ws, 4403)


# ================================================================
# F5 — the ONLY grace path left: pre-0025 rows (stored fp NULL)
# ================================================================


async def test_pre0025_null_fingerprint_row_keeps_grace_path(db):
    """Rows minted before migration 0025 recorded nothing, so there is no
    presentation to require — they redeem exactly as before."""
    app = _mk(db)
    _seed_sync()
    con = __import__("sqlite3").connect(DB_FILE)
    h = hashlib.sha256(b"pre-0025-ticket").hexdigest()
    later = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
    con.execute(
        "INSERT INTO ws_tickets (token_hash, user_id, team_id, role, project_id,"
        " expires_at, created_at, fingerprint)"
        " VALUES (?, 'u1', 't1', 'ADMIN', 'proj-1', ?, ?, NULL)",
        (h, later, datetime.now(UTC).isoformat()),
    )
    con.commit()
    con.close()
    async with MiniWs(app, "/api/v1/ws/proj-1?ticket=pre-0025-ticket") as ws:
        assert ws.accepted


# ================================================================
# F7 — ?token= long-lived fallback: deprecated + warned, then gone
# ================================================================


async def test_token_query_param_still_connects_with_deprecation_warning(db, caplog):
    """One-release deprecation law: the channel still works but screams.
    Next release removes it — legacy callers must route through /ws-ticket."""
    app = _mk(db)
    _seed_sync()
    with caplog.at_level(logging.WARNING, logger="harness_identity.app"):
        async with MiniWs(app, "/api/v1/ws/proj-1?token=goodtok") as ws:
            assert ws.accepted
    assert any("DEPRECATED" in r.message for r in caplog.records)
    assert any("ws-ticket" in r.message for r in caplog.records)


async def test_bad_token_query_param_still_rejected(db):
    app = _mk(db)
    _seed_sync()
    async with MiniWs(app, "/api/v1/ws/proj-1?token=bogus") as ws:
        _expect_reject(ws, 4401)


async def test_bearer_header_path_unaffected_by_f7(db):
    """The Authorization-header channel was never the finding — untouched."""
    app = _mk(db)
    _seed_sync()
    async with MiniWs(
        app,
        "/api/v1/ws/proj-1",
        headers={"Authorization": "Bearer goodtok"},
    ) as ws:
        assert ws.accepted
