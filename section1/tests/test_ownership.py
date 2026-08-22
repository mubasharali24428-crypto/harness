"""H2r — login ownership proof (OTP) behind HARNESS_REQUIRE_OWNERSHIP.

Security goals:
- No account takeover by knowing an email (passwordless login gated behind flag).
- Enumeration-safe: request-login answers 200 for unknown emails too.
- OTP: single-use, 10-min expiry, max 5 wrong attempts, code stored hashed.
"""

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from harness_identity.app import make_app
from harness_identity.config import Settings
from harness_identity.models import Base

DB_DIR = Path("/tmp/opencode")
DB_FILE = DB_DIR / "ownership_test.db"


@pytest.fixture
def db():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    if DB_FILE.exists():
        DB_FILE.unlink()
    sync = create_engine(f"sqlite:///{DB_FILE}")
    Base.metadata.create_all(sync)
    sync.dispose()
    eng = create_async_engine(f"sqlite+aiosqlite:///{DB_FILE}")
    yield async_sessionmaker(eng, expire_on_commit=False)
    if DB_FILE.exists():
        DB_FILE.unlink()


def _client(db, require_ownership=True):
    settings = Settings(
        master_key=b"test-master-key-0123456789abcdef",
        cors_origins=[],
        require_ownership=require_ownership,
    )
    app = make_app(db, settings)
    sent: list[tuple[str, str]] = []
    app.state.notifier = lambda email, code: sent.append((email, code))
    return TestClient(app), sent


def _seed_user(db):
    """Pre-create the target account so verify must attach to EXISTING user."""
    con = sqlite3.connect(DB_FILE)
    con.execute(
        "INSERT INTO users (id, email, name, is_active) VALUES ('u1','own@x.com','Own',1)"
    )
    con.commit()
    con.close()


def test_legacy_login_blocked_when_flag_on(db):
    client, _ = _client(db)
    r = client.post("/api/v1/auth/login", json={"email": "any@x.com"})
    assert r.status_code == 403
    detail = r.json().get("detail", {})
    assert (detail.get("error") or {}).get("code") == "OWNERSHIP_REQUIRED"


def test_legacy_login_works_without_flag(db):
    client, _ = _client(db, require_ownership=False)
    r = client.post("/api/v1/auth/login", json={"email": "fresh@x.com"})
    assert r.status_code == 200


def test_request_login_is_enumeration_safe(db):
    """Unknown emails get the same 200 shape as known ones — and no crash."""
    client, sent = _client(db)
    for email in ("ghost@nowhere.test", "own@x.com"):
        r = client.post("/api/v1/auth/request-login", json={"email": email})
        assert r.status_code == 200
        assert r.json()["ok"] is True
    assert {e for e, _ in sent} == {"ghost@nowhere.test", "own@x.com"}


def test_verify_happy_path_attaches_to_existing_user(db):
    _seed_user(db)
    client, sent = _client(db)
    client.post("/api/v1/auth/request-login", json={"email": "own@x.com"})
    code = sent[0][1]
    r = client.post(
        "/api/v1/auth/verify-otp", json={"email": "own@x.com", "code": code}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token"]
    assert body["principal"]["userId"] == "u1"
    # New solo team + demo project created on first successful login.
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200


def test_verify_rejects_wrong_code_and_counts_attempts(db):
    _seed_user(db)
    client, sent = _client(db)
    client.post("/api/v1/auth/request-login", json={"email": "own@x.com"})
    real_code = sent[0][1]
    for _ in range(5):
        r = client.post(
            "/api/v1/auth/verify-otp", json={"email": "own@x.com", "code": "000000"}
        )
        assert r.status_code == 401
    # Even the REAL code is now dead — brute force lockout.
    r = client.post("/api/v1/auth/verify-otp", json={"email": "own@x.com", "code": real_code})
    assert r.status_code == 401


def test_verify_is_single_use(db):
    _seed_user(db)
    client, sent = _client(db)
    client.post("/api/v1/auth/request-login", json={"email": "own@x.com"})
    code = sent[0][1]
    ok = client.post("/api/v1/auth/verify-otp", json={"email": "own@x.com", "code": code})
    assert ok.status_code == 200
    replay = client.post("/api/v1/auth/verify-otp", json={"email": "own@x.com", "code": code})
    assert replay.status_code == 401


def test_expired_code_rejected(db):
    _seed_user(db)
    client, sent = _client(db)
    client.post("/api/v1/auth/request-login", json={"email": "own@x.com"})
    code = sent[0][1]
    # Age the OTP past expiry directly in the store.
    con = sqlite3.connect(DB_FILE)
    con.execute(
        "UPDATE login_otps SET expires_at = ?",
        [(datetime.now(UTC) - timedelta(minutes=1)).isoformat()],
    )
    con.commit()
    con.close()
    r = client.post("/api/v1/auth/verify-otp", json={"email": "own@x.com", "code": code})
    assert r.status_code == 401


def test_codes_are_stored_hashed(db):
    _seed_user(db)
    client, sent = _client(db)
    client.post("/api/v1/auth/request-login", json={"email": "own@x.com"})
    raw_code = sent[0][1]
    con = sqlite3.connect(DB_FILE)
    rows = con.execute("SELECT code_hash FROM login_otps").fetchall()
    con.close()
    assert rows and all(raw_code != (r[0] or "") for r in rows)
    assert any(r[0] == hashlib.sha256(raw_code.encode()).hexdigest() for r in rows)


def test_new_email_via_otp_creates_account(db):
    """First successful verify for an unknown email provisions user+solo team,
    exactly like legacy login did — ownership was proven via the inbox."""
    client, sent = _client(db)
    client.post("/api/v1/auth/request-login", json={"email": "newbie@x.com"})
    code = sent[0][1]
    r = client.post("/api/v1/auth/verify-otp", json={"email": "newbie@x.com", "code": code})
    assert r.status_code == 200
    tok = r.json()["token"]
    projects = client.get("/api/v1/projects", headers={"Authorization": f"Bearer {tok}"})
    assert projects.status_code == 200
