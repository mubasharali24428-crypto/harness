"""Wave9-E tests: security headers middleware + secrets-redaction regression.

Node E owns ONLY this file (conftest.py is frozen this wave) — every fixture is
defined locally. Two concerns:

1. Header assertions through TestClient against the real identity app:
   static headers everywhere, HSTS strictly behind HARNESS_TLS_ENABLED,
   report-only CSP on HTML responses only.
2. Redaction regression guards encoding the Wave9-E crypto.py audit verdict
   (no gap found): encrypt-at-rest invariants + proof that a stored key's
   plaintext never lands in captured log output or audit_log rows.
"""

import base64
import hashlib
import logging
import uuid

import pytest
from cryptography.fernet import Fernet
from harness_contracts import KeyVault
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.app import make_app
from harness_identity.config import Settings
from harness_identity.crypto import read_api_key, store_api_key
from harness_identity.models import AuditLog, Base
from harness_identity.security_headers import (
    SecurityHeadersMiddleware,
    install_security_headers,
)

# Unique canary: grepping test output for it must stay boring forever.
CANARY_KEY = f"sk-canary-{uuid.uuid4().hex}-do-not-log"
MASTER = b"test-master-key-0123456789abcdef"


def _settings(**kw) -> Settings:
    return Settings(master_key=MASTER, cors_origins=[], **kw)


@pytest.fixture
async def header_db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
async def client(header_db):
    app = make_app(header_db, _settings())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.app = app
        yield c


async def _login(client: AsyncClient) -> dict:
    r = await client.post("/api/v1/auth/login", json={"email": "wave9-e@example.com"})
    assert r.status_code == 200
    return r.json()


# --------------------------------------------------------------------------
# Security headers through the real app
# --------------------------------------------------------------------------


async def test_static_security_headers_on_api_response(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"


async def test_hsts_absent_when_tls_flag_unset(client, monkeypatch):
    monkeypatch.delenv("HARNESS_TLS_ENABLED", raising=False)
    r = await client.get("/health")
    assert "strict-transport-security" not in r.headers


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
async def test_hsts_present_when_tls_enabled(client, monkeypatch, value):
    monkeypatch.setenv("HARNESS_TLS_ENABLED", value)
    r = await client.get("/health")
    assert r.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


@pytest.mark.parametrize("value", ["0", "false", "", "off"])
async def test_hsts_absent_for_falsy_flag_values(client, monkeypatch, value):
    monkeypatch.setenv("HARNESS_TLS_ENABLED", value)
    r = await client.get("/health")
    assert "strict-transport-security" not in r.headers


async def test_csp_report_only_on_html_routes_only(client):
    # Route registered AFTER middleware wiring: proves the CSP branch keys off
    # the response content-type, not route registration order.
    from fastapi.responses import HTMLResponse

    @client.app.get("/wave9-html-probe")
    async def wave9_html_probe():
        return HTMLResponse("<html><body>wave9-e</body></html>")

    r = await client.get("/wave9-html-probe")
    csp = r.headers["content-security-policy-report-only"]
    assert csp.startswith("default-src 'self'")
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp

    # JSON API responses must NOT carry the HTML CSP header.
    r = await client.get("/health")
    assert "content-security-policy-report-only" not in r.headers


def test_install_factory_registers_and_returns_middleware_class():
    calls: list[object] = []

    class Dummy:
        def add_middleware(self, cls):
            calls.append(cls)

    dummy = Dummy()
    assert install_security_headers(dummy) is SecurityHeadersMiddleware
    assert calls == [SecurityHeadersMiddleware]


def test_installed_apps_carry_the_middleware(header_db):
    app = make_app(header_db, _settings())
    assert any(m.cls is SecurityHeadersMiddleware for m in app.user_middleware)


# --------------------------------------------------------------------------
# Crypto.py / KeyVault write-path audit verdict (NO gap found), as regressions
# --------------------------------------------------------------------------


def test_vault_uses_random_iv_per_encryption():
    v = KeyVault(MASTER)
    ct1, ct2 = v.encrypt(CANARY_KEY), v.encrypt(CANARY_KEY)
    assert ct1 != ct2  # fresh random IV per encrypt() call
    raw = base64.urlsafe_b64decode(ct1.encode())
    assert raw[0] == 0x80  # Fernet version/magic byte
    assert len(raw[9:25]) == 16 and raw[9:25] != b"\x00" * 16  # AES-CBC IV slot


def test_vault_key_derivation_is_sha256_of_master():
    # A vault rebuilt from the documented derivation (SHA-256(master)) must
    # decrypt tokens written by KeyVault — pins the KDF against silent change.
    derived = Fernet(base64.urlsafe_b64encode(hashlib.sha256(MASTER).digest()))
    v = KeyVault(MASTER)
    token = v.encrypt(CANARY_KEY)
    assert derived.decrypt(token.encode()).decode() == CANARY_KEY
    assert v.decrypt(token) == CANARY_KEY  # and the roundtrip holds


def test_key_hint_leaks_nothing_beyond_last_four_convention():
    hint = KeyVault.hint(CANARY_KEY)
    assert hint == "…" + CANARY_KEY[-4:]  # existing convention, unchanged
    assert len(hint) <= 8  # fits api_keys.key_hint VARCHAR(8)
    assert CANARY_KEY not in hint
    assert CANARY_KEY[:-4] not in hint  # everything before the last 4 chars stays hidden
    assert KeyVault.hint("abc") == "…"  # too short to show a suffix


async def test_store_api_key_redacts_plaintext_from_logs_and_audit_rows(
    header_db, client_ownership_off, caplog
):
    client = client_ownership_off
    """Wave9-E redaction regression: storing a key leaves ZERO traces of the
    plaintext in captured log output or in audit_log rows."""
    data = await _login(client)
    team = data["principal"]["teamId"]

    with caplog.at_level(logging.DEBUG):
        async with header_db() as s:
            row = await store_api_key(s, client.app.state.vault, team, "openai", "k-w9", CANARY_KEY)

    # 1. No log record emitted anywhere in the write path carries the secret.
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert CANARY_KEY not in logged

    # 2. Stored row: ciphertext only; hint obeys the last-4 convention.
    assert CANARY_KEY not in row.key_encrypted
    assert row.key_hint == "…" + CANARY_KEY[-4:]

    # 3. audit_log rows (write_audit validates actions; nothing else may land).
    async with header_db() as s:
        rows = (await s.execute(select(AuditLog))).scalars().all()
    blob = repr([(r.action, r.target_type, r.target_id, r.detail) for r in rows])
    assert CANARY_KEY not in blob

    # 4. Roundtrip still works — redaction must not break the happy path.
    async with header_db() as s:
        assert await read_api_key(s, client.app.state.vault, row.id) == CANARY_KEY


async def test_read_api_key_error_never_echoes_secret_values(header_db, client_ownership_off):
    client = client_ownership_off
    data = await _login(client)
    team = data["principal"]["teamId"]
    async with header_db() as s:
        row = await store_api_key(s, client.app.state.vault, team, "openai", "k-x", CANARY_KEY)
    with pytest.raises(LookupError) as excinfo:
        async with header_db() as s:
            await read_api_key(s, client.app.state.vault, "no-such-id")
    assert CANARY_KEY not in str(excinfo.value)
    assert row.id  # silence unused-var lints while keeping the write real
