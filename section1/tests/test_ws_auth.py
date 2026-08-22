import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from harness_identity.app import make_app
from harness_identity.config import Settings
from harness_identity.models import Base

DB_DIR = Path("/tmp/opencode")
DB_FILE = DB_DIR / "ws_auth_test.db"


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
    con = sqlite3.connect(DB_FILE)
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


def _client(db):
    app = make_app(db, Settings(master_key=b"test-master-key-0123456789abcdef", cors_origins=[]))
    return TestClient(app)


def test_ws_rejects_unauthenticated_client(db):
    client = _client(db)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/ws/proj-1"):
            pass


def test_ws_rejects_bad_token(db):
    client = _client(db)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/ws/proj-1?token=bogus"):
            pass


def test_ws_accepts_valid_token(db):
    _seed_sync()
    client = _client(db)
    with client.websocket_connect("/api/v1/ws/proj-1?token=goodtok") as ws:
        assert ws is not None


def test_ws_rejects_foreign_team_project(db):
    """F3 regression guard: valid token, but project belongs to another team."""
    import sqlite3 as _s

    con = _s.connect(DB_FILE)
    con.executescript(
        """
        INSERT INTO teams (id, name) VALUES ('t2','OTHER');
        INSERT INTO projects (id, team_id, name) VALUES ('proj-other','t2','secret');
        """
    )
    con.commit()
    con.close()
    client = _client(db)
    from starlette.websockets import WebSocketDisconnect as WD

    with pytest.raises(WD):
        with client.websocket_connect("/api/v1/ws/proj-other?token=goodtok"):
            pass


# ================================================================
# H8: one-time WS tickets replace query-string tokens
# ================================================================


def _app_and_client(db):
    app = make_app(
        db, Settings(master_key=b"test-master-key-0123456789abcdef", cors_origins=[])
    )
    return app, TestClient(app)


def _issue_ticket(client, project_id="proj-1", token="goodtok"):
    r = client.post(
        "/api/v1/ws-ticket",
        json={"projectId": project_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    return r


def test_ws_ticket_requires_auth(db):
    _, client = _app_and_client(db)
    _seed_sync()
    r = client.post("/api/v1/ws-ticket", json={"projectId": "proj-1"})
    assert r.status_code == 401


def test_ws_ticket_rejects_foreign_project(db):
    """F3 guard extended to tickets: cannot mint a ticket for another team's project."""
    import sqlite3 as _s

    _seed_sync()
    con = _s.connect(DB_FILE)
    con.executescript(
        """
        INSERT INTO teams (id, name) VALUES ('t2','OTHER');
        INSERT INTO projects (id, team_id, name) VALUES ('proj-other','t2','secret');
        """
    )
    con.commit()
    con.close()
    _, client = _app_and_client(db)
    assert _issue_ticket(client, project_id="proj-other").status_code == 403


def test_ws_ticket_happy_path(db):
    app, client = _app_and_client(db)
    _seed_sync()
    r = _issue_ticket(client)
    assert r.status_code == 200
    body = r.json()
    assert body["ticket"]
    assert body["expiresInSeconds"] > 0
    with client.websocket_connect(
        f"/api/v1/ws/proj-1?ticket={body['ticket']}"
    ) as ws:
        assert ws is not None


def test_ws_ticket_is_single_use(db):
    _, client = _app_and_client(db)
    _seed_sync()
    ticket = _issue_ticket(client).json()["ticket"]

    from starlette.websockets import WebSocketDisconnect as WD

    # First use MUST succeed...
    with client.websocket_connect(f"/api/v1/ws/proj-1?ticket={ticket}") as ws:
        assert ws is not None
    # ...and the replayed ticket MUST be rejected.
    with pytest.raises(WD):
        with client.websocket_connect(f"/api/v1/ws/proj-1?ticket={ticket}"):
            pass


def test_ws_ticket_bound_to_project(db):
    _, client = _app_and_client(db)
    _seed_sync()
    con = _s.connect(DB_FILE) if (_s := __import__("sqlite3")) else None
    con.executescript(
        """
        INSERT INTO teams (id, name) VALUES ('t2','OTHER');
        INSERT INTO projects (id, team_id, name) VALUES ('proj-other','t2','secret');
        """
    )
    con.commit()
    con.close()
    ticket = _issue_ticket(client, project_id="proj-1").json()["ticket"]

    from starlette.websockets import WebSocketDisconnect as WD

    with pytest.raises(WD):
        with client.websocket_connect(f"/api/v1/ws/proj-other?ticket={ticket}"):
            pass


def test_ws_ticket_unknown_rejected(db):
    _, client = _app_and_client(db)
    _seed_sync()

    from starlette.websockets import WebSocketDisconnect as WD

    with pytest.raises(WD):
        with client.websocket_connect("/api/v1/ws/proj-1?ticket=bogus"):
            pass


def test_ws_ticket_expired_rejected(db):
    from datetime import UTC, datetime, timedelta

    app, client = _app_and_client(db)
    _seed_sync()
    ticket = _issue_ticket(client).json()["ticket"]
    store = app.state.ws_tickets
    store[ticket]["expires"] = datetime.now(UTC) - timedelta(seconds=1)

    from starlette.websockets import WebSocketDisconnect as WD

    with pytest.raises(WD):
        with client.websocket_connect(f"/api/v1/ws/proj-1?ticket={ticket}"):
            pass


def test_token_path_still_works_during_transition(db):
    _, client = _app_and_client(db)
    _seed_sync()
    with client.websocket_connect("/api/v1/ws/proj-1?token=goodtok") as ws:
        assert ws is not None
