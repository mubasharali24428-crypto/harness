"""Mission 22 M4 — OIDC SSO hooks tests.

Everything runs offline EXCEPT one real localhost HTTP server: the mock IdP
fixture serves /.well-known/openid-configuration, /jwks.json and POST /token
over a bound socket so sso.py's httpx calls exercise REAL discovery + JWKS
fetches and a REAL token exchange (the mission's "PKCE flow against a mock
IdP" gate).

Coverage, mapped to the fix scope:
  1. discovery parse        -> test_discovery_* (+ issuer-mismatch / bad shape)
  2. PKCE flow vs mock IdP  -> test_full_pkce_flow_against_mock_idp + negatives
                               (replayed state, redirect mismatch, expired
                               state, IdP-rejected exchange)
  3. email-match mapping    -> test_stranger_*, test_member_of_another_team_*,
                               case-insensitive match; role from membership
  4. honest OTP fallback    -> test_unconfigured_team_falls_back_to_otp_honestly
  + config CRUD authz/tier gates/audit rows/secret hygiene; RS256 signatures
  come from a REAL RSA keypair generated in-process (no jwt library anywhere).
"""

import base64
import hashlib
import json
import secrets
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.auth import get_principal, issue_otp, verify_otp
from harness_identity.crypto import KeyVault
from harness_identity.http_client import env_client
from harness_identity.models import (
    AuditLog,
    AuthSession,
    Base,
    Team,
    TeamMember,
    User,
)
from harness_identity.sso import (
    REQUIRED_DISCOVERY_KEYS,
    SsoError,
    SsoLoginState,
    TeamSsoConfig,
    fetch_discovery,
    generate_pkce_pair,
    router,
    validate_id_token,
)

MASTER_KEY = b"test-master-key-0123456789abcdef"
ISSUER = "https://idp.example.test"
CLIENT_ID = "harness-sso-client"
# gitleaks:allow — fake fixture value, never a real credential
CLIENT_SECRET = "idp-client-secret-0123456789"
REDIRECT_URI = "http://localhost:3000/login/callback"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


# --------------------------------------------------------------------------
# RSA material for REAL RS256 signatures
# --------------------------------------------------------------------------

_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_RSA_PUB = _RSA_KEY.public_key().public_numbers()
JWK = {
    "kty": "RSA",
    "kid": "test-key-1",
    "use": "sig",
    "alg": "RS256",
    "n": _b64url(_RSA_PUB.n.to_bytes((_RSA_PUB.n.bit_length() + 7) // 8, "big")),
    "e": _b64url(_RSA_PUB.e.to_bytes(3, "big")),
}


def make_id_token(
    claims: dict,
    *,
    key=_RSA_KEY,
    kid: str = "test-key-1",
    alg: str = "RS256",
) -> str:
    """Sign claims as a compact JWS (RS256) with the in-process RSA key."""
    header = {"alg": alg, "typ": "JWT", "kid": kid}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(claims, separators=(",", ":")).encode())
    )
    sig = key.sign(signing_input.encode(), asym_padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + _b64url(sig)


# --------------------------------------------------------------------------
# Mock IdP: a real localhost HTTP server speaking discovery/JWKS/token
# --------------------------------------------------------------------------


class MockIdP:
    """Serves the three OIDC documents; records token-exchange requests."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.fail_next_token = False
        self.pending_id_token: str | None = None
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    class _Handler(BaseHTTPRequestHandler):
        idp: "MockIdP"  # injected via subclass at start()

        def log_message(self, fmt, *args):  # noqa: ANN001 — silence request logs
            pass

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 — stdlib interface
            if self.path == "/.well-known/openid-configuration":
                base = self.idp.base_url  # the REAL bound URL is the issuer
                self._json(
                    {
                        "issuer": base,
                        "authorization_endpoint": f"{base}/authorize",
                        "token_endpoint": f"{base}/token",
                        "jwks_uri": f"{base}/jwks.json",
                        "unknown_extra_key": "tolerated-by-parse",
                    }
                )
            elif self.path == "/jwks.json":
                self._json({"keys": [JWK]})
            else:
                self._json({"error": "not found"}, status=404)

        def do_POST(self):  # noqa: N802 — stdlib interface
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode()
            form = {
                k: unquote(v)
                for k, v in (kv.split("=", 1) for kv in raw.split("&") if "=" in kv)
            }
            self.idp.requests.append(form)
            if self.idp.fail_next_token:
                self._json({"error": "invalid_grant"}, status=400)
                return
            self._json(
                {
                    "access_token": "at-fake",
                    "token_type": "Bearer",
                    "id_token": self.idp.pending_id_token or "",
                }
            )

    def start(self) -> None:
        handler = type("_BoundHandler", (self._Handler,), {"idp": self})
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    @property
    def issuer_url(self) -> str:
        return self.base_url


@pytest.fixture
async def mock_idp():
    idp = MockIdP()
    idp.start()
    yield idp
    idp.stop()


# --------------------------------------------------------------------------
# App fixtures (scratch app mounting ONLY the sso router, house convention)
# --------------------------------------------------------------------------


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def client(factory):
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = factory
    app.state.vault = KeyVault(MASTER_KEY)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _error_code(r) -> str:
    body = r.json()
    err = body.get("error") or (body.get("detail") or {}).get("error")
    return (err or {}).get("code", "<missing>")


async def _seed_team_admin(factory, *, plan: str | None = "team") -> dict:
    """Team + admin user/membership/session (+ billing_plans row when asked)."""
    async with factory() as db:
        team = Team(name="sso-team")
        db.add(team)
        await db.flush()
        user = User(email="admin@x.com", name="Admin")
        db.add(user)
        await db.flush()
        db.add(
            TeamMember(team_id=team.id, user_id=user.id, display_name="Admin", role="admin")
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
        if plan is not None:
            from decimal import Decimal

            from harness_identity.billing import BillingPlan

            db.add(BillingPlan(team_id=team.id, plan=plan, monthly_budget_usd=Decimal("0")))
        await db.commit()
    return {"teamId": team.id, "userId": user.id, "token": token}


async def _seed_member(factory, team_id: str, email: str, role: str = "engineer") -> dict:
    async with factory() as db:
        user = User(email=email.lower(), name=email.split("@")[0])
        db.add(user)
        await db.flush()
        member = TeamMember(
            team_id=team_id, user_id=user.id, display_name=user.name, role=role
        )
        db.add(member)
        await db.commit()
        return {"userId": user.id, "memberId": member.id}


async def _configure_sso(client, admin: dict, issuer_url: str, **overrides) -> dict:
    r = await client.put(
        f"/api/v1/teams/{admin['teamId']}/sso/config",
        json={
            "issuerUrl": issuer_url,
            "clientId": CLIENT_ID,
            "clientSecret": CLIENT_SECRET,
            **overrides,
        },
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _start_login(client, team_id: str, *, redirect_uri: str = REDIRECT_URI) -> dict:
    """POST sso/start; returns the parsed authorization-query params."""
    r = await client.post(
        "/api/v1/auth/sso/start",
        json={"teamId": team_id, "redirectUri": redirect_uri},
    )
    assert r.status_code == 200, r.text
    url = r.json()["authorizationUrl"]
    scheme, rest = url.split("?", 1)
    query = {k: v[0] for k, v in parse_qs(rest).items()}
    query["_endpoint"] = scheme
    return query


async def _complete(client, team_id: str, state: str, code: str, **over) -> object:
    payload = {
        "teamId": team_id,
        "code": code,
        "state": state,
        "redirectUri": REDIRECT_URI,
        **over,
    }
    return await client.post("/api/v1/auth/sso/complete", json=payload)


async def _audit_rows(factory, action: str) -> list[AuditLog]:
    async with factory() as db:
        rows = (
            (await db.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all()
        )
        return list(rows)


# --------------------------------------------------------------------------
# 1. Discovery parse
# --------------------------------------------------------------------------


def test_generate_pkce_pair_shape():
    verifier, challenge = generate_pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert challenge == _b64url(hashlib.sha256(verifier.encode()).digest())
    assert challenge != verifier
    assert generate_pkce_pair()[0] != verifier  # fresh pairs never repeat


async def test_discovery_parses_via_real_http(mock_idp):
    async with env_client() as http:  # the product's factory: a bracketed NO_PROXY can't break it
        doc = await fetch_discovery(http, mock_idp.issuer_url)
    assert doc["issuer"] == mock_idp.issuer_url  # the REAL bound URL
    assert all(doc[k] for k in REQUIRED_DISCOVERY_KEYS)


class _StubResp:
    """Minimal httpx.Response double for the pure parse/validation tests."""

    def __init__(self, payload) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self._payload


class _StubClient:
    def __init__(self, payload) -> None:
        self._payload = payload

    async def get(self, url):
        return _StubResp(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_discovery_rejects_mismatched_issuer():
    evil = {
        "issuer": "https://evil.example",
        "authorization_endpoint": "https://evil.example/a",
        "token_endpoint": "https://evil.example/t",
        "jwks_uri": "https://evil.example/j",
    }
    with pytest.raises(SsoError, match="issuer mismatch"):
        await fetch_discovery(_StubClient(evil), ISSUER)  # type: ignore[arg-type]


async def test_discovery_rejects_missing_or_empty_keys():
    incomplete = {"issuer": ISSUER}
    with pytest.raises(SsoError, match="missing keys"):
        await fetch_discovery(_StubClient(incomplete), ISSUER)  # type: ignore[arg-type]
    empty = {"issuer": "", "authorization_endpoint": "", "token_endpoint": "",
             "jwks_uri": ""}
    with pytest.raises(SsoError, match="missing keys"):
        await fetch_discovery(_StubClient(empty), ISSUER)  # type: ignore[arg-type]
    with pytest.raises(SsoError, match="unreachable"):
        await fetch_discovery(_StubClient(None), ISSUER)  # type: ignore[arg-type]


async def test_jwks_fetch_requires_keys():
    from harness_identity.sso import fetch_jwks

    with pytest.raises(SsoError, match="no keys"):
        await fetch_jwks(_StubClient({}), f"{ISSUER}/jwks.json")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# ID-token validation (real RS256)
# --------------------------------------------------------------------------


def _claims(idp=None, **over) -> dict:
    """Default claims for the STATIC issuer (pure-validation tests). For mock
    IdP flows pass ``idp=mock_idp`` so iss matches the REAL bound URL."""
    now = datetime.now(UTC)
    base = {
        "iss": idp.issuer_url if idp is not None else ISSUER,
        "aud": CLIENT_ID,
        "sub": "user-123",
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "nonce": "nonce-abc",
        "email": "eng@x.com",
    }
    base.update(over)
    return base


def test_validate_id_token_happy_path():
    jwks = {"keys": [JWK]}
    out = validate_id_token(
        make_id_token(_claims()), jwks, issuer=ISSUER, audience=CLIENT_ID, nonce="nonce-abc"
    )
    assert out["sub"] == "user-123"
    assert out["email"] == "eng@x.com"


def test_validate_id_token_rejects_every_failure_shape():
    jwks = {"keys": [JWK]}
    foreign_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    forged = make_id_token(_claims(), key=foreign_key)  # signed by another key
    with pytest.raises(SsoError, match="signature"):
        validate_id_token(forged, jwks, issuer=ISSUER, audience=CLIENT_ID, nonce=None)

    tampered = make_id_token(_claims()).rsplit(".", 1)[0] + ".AAAA"
    with pytest.raises(SsoError):
        validate_id_token(tampered, jwks, issuer=ISSUER, audience=CLIENT_ID, nonce=None)

    expired = make_id_token(
        _claims(exp=int((datetime.now(UTC) - timedelta(hours=1)).timestamp()))
    )
    with pytest.raises(SsoError, match="expired"):
        validate_id_token(expired, jwks, issuer=ISSUER, audience=CLIENT_ID, nonce=None)

    wrong_iss = make_id_token(_claims(iss="https://someone-else.example"))
    with pytest.raises(SsoError, match="issuer"):
        validate_id_token(wrong_iss, jwks, issuer=ISSUER, audience=CLIENT_ID, nonce=None)

    wrong_aud = make_id_token(_claims(aud="another-client"))
    with pytest.raises(SsoError, match="audience"):
        validate_id_token(wrong_aud, jwks, issuer=ISSUER, audience=CLIENT_ID, nonce=None)

    wrong_nonce = make_id_token(_claims(nonce="other"))
    with pytest.raises(SsoError, match="nonce"):
        validate_id_token(wrong_nonce, jwks, issuer=ISSUER, audience=CLIENT_ID, nonce="expected")

    hs_alg = make_id_token(_claims(), alg="HS256")
    with pytest.raises(SsoError, match="algorithm"):
        validate_id_token(hs_alg, jwks, issuer=ISSUER, audience=CLIENT_ID, nonce=None)

    with pytest.raises(SsoError, match="malformed"):
        validate_id_token("not-a-jwt", jwks, issuer=ISSUER, audience=CLIENT_ID, nonce=None)


def test_jwk_selection_rules():
    from harness_identity.sso import _select_rsa_jwk

    second = {**JWK, "kid": "other-key"}
    assert _select_rsa_jwk({"keys": [JWK, second]}, "other-key")["kid"] == "other-key"
    assert _select_rsa_jwk({"keys": [JWK, second]}, "unknown-kid") is None
    # No kid in header + two candidates -> ambiguous -> refused.
    assert _select_rsa_jwk({"keys": [JWK, second]}, None) is None
    assert _select_rsa_jwk({"keys": [JWK]}, None)["kid"] == "test-key-1"


# --------------------------------------------------------------------------
# 2. Config CRUD: authz, tier gate, secret hygiene, audit rows
# --------------------------------------------------------------------------


async def test_config_crud_secret_never_leaks_and_audit_rows_flow(factory, client):
    admin = await _seed_team_admin(factory, plan="team")

    # create REQUIRES clientSecret on the first PUT
    r = await client.put(
        f"/api/v1/teams/{admin['teamId']}/sso/config",
        json={"issuerUrl": ISSUER, "clientId": CLIENT_ID},
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 422

    created = await _configure_sso(client, admin, ISSUER)
    assert created["configured"] is True
    assert created["clientId"] == CLIENT_ID
    assert CLIENT_SECRET not in str(created), "raw secret must NEVER leave the server"
    assert len(created["secretHint"]) <= 5

    # update WITHOUT clientSecret keeps the stored secret working
    updated = await _configure_sso(client, admin, ISSUER + "/", idpDisplayName="Okta")
    assert updated["issuerUrl"] == ISSUER.rstrip("/")
    assert updated["idpDisplayName"] == "Okta"

    # GET read-back is hint-only too
    r = await client.get(
        f"/api/v1/teams/{admin['teamId']}/sso/config", headers=_bearer(admin["token"])
    )
    assert CLIENT_SECRET not in r.text

    saved = await _audit_rows(factory, "sso.config_saved")
    assert [row.detail.get("created") for row in saved] == [True, False]
    assert await _audit_rows(factory, "sso.config_deleted") == []

    # DELETE removes it; repeat stays 204 (idempotent); audit exactly once
    for _ in range(2):
        r = await client.delete(
            f"/api/v1/teams/{admin['teamId']}/sso/config", headers=_bearer(admin["token"])
        )
        assert r.status_code == 204
    assert len(await _audit_rows(factory, "sso.config_deleted")) == 1
    r = await client.get(
        f"/api/v1/teams/{admin['teamId']}/sso/config", headers=_bearer(admin["token"])
    )
    assert r.json()["configured"] is False


async def test_config_gated_to_admin_own_team_and_tier(factory, client):
    admin = await _seed_team_admin(factory, plan=None)  # NOT team tier yet
    create_body = {
        "issuerUrl": ISSUER,
        "clientId": CLIENT_ID,
        "clientSecret": CLIENT_SECRET,
    }

    # in-team non-admin: FORBIDDEN wherever they probe
    async with factory() as db:
        engineer = User(email="eng@x.com", name="Eng")
        db.add(engineer)
        await db.flush()
        db.add(
            TeamMember(
                team_id=admin["teamId"], user_id=engineer.id,
                display_name="Eng", role="engineer",
            )
        )
        eng_token = secrets.token_urlsafe(32)
        db.add(
            AuthSession(
                token_hash=hash_token(eng_token),
                user_id=engineer.id,
                team_id=admin["teamId"],
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await db.commit()
    r = await client.put(
        f"/api/v1/teams/{admin['teamId']}/sso/config",
        json=create_body,
        headers=_bearer(eng_token),
    )
    assert r.status_code == 403
    assert _error_code(r) == "FORBIDDEN"

    # free-tier admin: PLAN_REQUIRED (SSO is the paid feature; fail-closed)
    r = await client.put(
        f"/api/v1/teams/{admin['teamId']}/sso/config",
        json=create_body,
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 403
    assert _error_code(r) == "PLAN_REQUIRED"

    # upgrade to team tier -> accepted (create the row: the seed ran with
    # plan=None, so no BillingPlan exists to UPDATE)
    from decimal import Decimal

    from harness_identity.billing import BillingPlan

    async with factory() as db:
        existing = (
            await db.execute(
                select(BillingPlan).where(BillingPlan.team_id == admin["teamId"])
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                BillingPlan(
                    team_id=admin["teamId"], plan="team", monthly_budget_usd=Decimal("0")
                )
            )
        else:
            existing.plan = "team"
        await db.commit()
    r = await client.put(
        f"/api/v1/teams/{admin['teamId']}/sso/config",
        json=create_body,
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 200, r.text

    # foreign team id answers exactly what an unknown one does (no oracle)
    r = await client.put(
        "/api/v1/teams/00000000-dead-beef-0000-000000000000/sso/config",
        json=create_body,
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 404
    assert _error_code(r) == "NOT_FOUND"


# --------------------------------------------------------------------------
# 3. The PKCE login flow against the MOCK IdP
# --------------------------------------------------------------------------


async def test_full_pkce_flow_against_mock_idp(factory, client, mock_idp):
    """THE gate: discovery -> authorize URL w/ PKCE -> real token exchange ->
    JWKS-validated ID token -> member session."""
    admin = await _seed_team_admin(factory, plan="team")
    await _seed_member(factory, admin["teamId"], "Eng@X.com", role="engineer")
    await _configure_sso(client, admin, mock_idp.issuer_url)

    query = await _start_login(client, admin["teamId"])
    assert query["_endpoint"].startswith(f"{mock_idp.issuer_url}/authorize")
    assert query["response_type"] == "code"
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"] == REDIRECT_URI
    assert query["code_challenge_method"] == "S256"
    assert "openid" in query["scope"]

    mock_idp.pending_id_token = make_id_token(
        _claims(idp=mock_idp,nonce=query["nonce"], email="eng@x.com")
    )
    r = await _complete(client, admin["teamId"], query["state"], "auth-code-1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token"]
    assert body["principal"]["role"] == "engineer"  # membership role, not IdP's word
    assert body["principal"]["teamId"] == admin["teamId"]

    # the exchange REALLY hit the mock IdP over HTTP with the right grant
    exchange = mock_idp.requests[-1]
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["code"] == "auth-code-1"
    assert exchange["client_id"] == CLIENT_ID
    assert exchange["client_secret"] == CLIENT_SECRET
    assert exchange["redirect_uri"] == REDIRECT_URI
    # PKCE binding: presented verifier hashes to the minted challenge
    verifier = exchange["code_verifier"]
    assert _b64url(hashlib.sha256(verifier.encode()).digest()) == query["code_challenge"]

    # the minted session works as a normal bearer on the shared auth surface
    me_app = FastAPI()

    @me_app.get("/whoami")
    async def whoami(principal=Depends(get_principal)):  # noqa: B008, ANN001
        return principal.model_dump()

    me_app.state.session_factory = factory
    async with AsyncClient(transport=ASGITransport(app=me_app), base_url="http://test") as mc:
        me = await mc.get("/whoami", headers=_bearer(body["token"]))
    assert me.status_code == 200
    assert me.json()["userId"] == body["principal"]["userId"]


async def test_state_is_single_use(factory, client, mock_idp):
    admin = await _seed_team_admin(factory, plan="team")
    await _seed_member(factory, admin["teamId"], "eng@x.com")
    await _configure_sso(client, admin, mock_idp.issuer_url)

    query = await _start_login(client, admin["teamId"])
    mock_idp.pending_id_token = make_id_token(
        _claims(idp=mock_idp, nonce=query["nonce"], email="eng@x.com")
    )
    ok = await _complete(client, admin["teamId"], query["state"], "code-1")
    assert ok.status_code == 200
    replay = await _complete(client, admin["teamId"], query["state"], "code-2")
    assert replay.status_code == 400
    assert _error_code(replay) == "SSO_STATE_INVALID"


async def test_redirect_uri_mismatch_fails_closed(factory, client, mock_idp):
    admin = await _seed_team_admin(factory, plan="team")
    await _seed_member(factory, admin["teamId"], "eng@x.com")
    await _configure_sso(client, admin, mock_idp.issuer_url)

    query = await _start_login(client, admin["teamId"])
    mock_idp.pending_id_token = make_id_token(_claims(idp=mock_idp,email="eng@x.com"))
    r = await _complete(
        client,
        admin["teamId"],
        query["state"],
        "code-x",
        redirectUri="https://evil.example/cb",
    )
    assert r.status_code == 400
    assert _error_code(r) == "SSO_STATE_INVALID"
    # nothing reached the IdP either
    assert mock_idp.requests == []


async def test_expired_state_fails_closed(factory, client, mock_idp, monkeypatch):
    import harness_identity.sso as sso_module

    admin = await _seed_team_admin(factory, plan="team")
    await _seed_member(factory, admin["teamId"], "eng@x.com")
    await _configure_sso(client, admin, mock_idp.issuer_url)

    monkeypatch.setattr(sso_module, "SSO_STATE_TTL_SECONDS", -1)
    query = await _start_login(client, admin["teamId"])
    monkeypatch.setattr(sso_module, "SSO_STATE_TTL_SECONDS", 600)
    mock_idp.pending_id_token = make_id_token(_claims(idp=mock_idp,email="eng@x.com"))
    r = await _complete(client, admin["teamId"], query["state"], "code-e")
    assert r.status_code == 400
    assert _error_code(r) == "SSO_STATE_INVALID"


async def test_idp_rejected_exchange_answers_honest_401(factory, client, mock_idp):
    """A code the IdP refuses (expired/wrong-verifier at the provider side)
    maps to one honest 401 — no crash, no partial session."""
    admin = await _seed_team_admin(factory, plan="team")
    await _seed_member(factory, admin["teamId"], "eng@x.com")
    await _configure_sso(client, admin, mock_idp.issuer_url)

    query = await _start_login(client, admin["teamId"])
    mock_idp.pending_id_token = make_id_token(_claims(idp=mock_idp,email="eng@x.com"))
    mock_idp.fail_next_token = True
    r = await _complete(client, admin["teamId"], query["state"], "dead-code")
    assert r.status_code == 401
    assert _error_code(r) == "SSO_TOKEN_EXCHANGE_FAILED"
    assert mock_idp.requests[-1]["code"] == "dead-code"
    assert mock_idp.requests[-1]["code_verifier"]  # verifier WAS presented


async def test_tampered_id_token_never_yields_a_session(factory, client, mock_idp):
    admin = await _seed_team_admin(factory, plan="team")
    await _seed_member(factory, admin["teamId"], "eng@x.com")
    await _configure_sso(client, admin, mock_idp.issuer_url)

    foreign_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    query = await _start_login(client, admin["teamId"])
    # IdP returns a token signed by an imposter key with a self-granted email
    mock_idp.pending_id_token = make_id_token(
        _claims(idp=mock_idp,nonce=query["nonce"], email="eng@x.com"), key=foreign_key
    )
    r = await _complete(client, admin["teamId"], query["state"], "code-f")
    assert r.status_code == 502  # SSO validation failures surface honestly...
    assert "signature" in r.text.lower() or "invalid" in r.text.lower()
    async with factory() as db:
        sessions = (await db.execute(select(func.count()).select_from(AuthSession))).scalar_one()
    assert sessions == 1  # ...and only the admin's seeded session exists


# --------------------------------------------------------------------------
# Email-match provisioning/mapping
# --------------------------------------------------------------------------


async def test_email_match_is_case_insensitive_and_role_from_membership(
    factory, client, mock_idp
):
    admin = await _seed_team_admin(factory, plan="team")
    await _seed_member(factory, admin["teamId"], "Viewer@X.com", role="viewer")
    await _configure_sso(client, admin, mock_idp.issuer_url)

    query = await _start_login(client, admin["teamId"])
    mock_idp.pending_id_token = make_id_token(
        _claims(idp=mock_idp,nonce=query["nonce"], email="VIEWER@x.com")  # case differs
    )
    r = await _complete(client, admin["teamId"], query["state"], "code-v")
    assert r.status_code == 200, r.text
    assert r.json()["principal"]["role"] == "viewer"


async def test_stranger_at_the_idp_gains_nothing(factory, client, mock_idp):
    """THE security property: an IdP identity matching no member creates no
    account, no seat, no session — joining stays invite-only."""
    admin = await _seed_team_admin(factory, plan="team")
    await _configure_sso(client, admin, mock_idp.issuer_url)

    query = await _start_login(client, admin["teamId"])
    mock_idp.pending_id_token = make_id_token(
        _claims(idp=mock_idp,nonce=query["nonce"], email="mallory@evil.com")
    )
    r = await _complete(client, admin["teamId"], query["state"], "code-m")
    assert r.status_code == 403
    assert _error_code(r) == "SSO_EMAIL_NOT_MEMBER"

    async with factory() as db:
        users = (await db.execute(select(func.count()).select_from(User))).scalar_one()
        members = (
            await db.execute(
                select(func.count())
                .select_from(TeamMember)
                .where(TeamMember.team_id == admin["teamId"])
            )
        ).scalar_one()
        sessions = (await db.execute(select(func.count()).select_from(AuthSession))).scalar_one()
    assert users == 1  # only the seeded admin
    assert members == 1  # only the admin's membership
    assert sessions == 1  # only the admin's seeded session


async def test_missing_email_claim_rejected(factory, client, mock_idp):
    admin = await _seed_team_admin(factory, plan="team")
    await _seed_member(factory, admin["teamId"], "eng@x.com")
    await _configure_sso(client, admin, mock_idp.issuer_url)
    query = await _start_login(client, admin["teamId"])
    mock_idp.pending_id_token = make_id_token(
        {k: v for k, v in _claims(idp=mock_idp, nonce=query["nonce"]).items() if k != "email"}
    )
    r = await _complete(client, admin["teamId"], query["state"], "c")
    assert r.status_code == 403
    assert _error_code(r) == "SSO_EMAIL_MISSING"


async def test_member_of_another_team_does_not_map(factory, client, mock_idp):
    """Email matches a USER of another team but not a member of THIS one ->
    403; cross-team login is impossible even with a shared IdP directory."""
    admin = await _seed_team_admin(factory, plan="team")
    async with factory() as db:
        other = Team(name="other-team")
        db.add(other)
        await db.flush()
        u = User(email="eng@x.com", name="Eng")
        db.add(u)
        await db.flush()
        db.add(TeamMember(team_id=other.id, user_id=u.id, display_name="Eng", role="engineer"))
        await db.commit()
    await _configure_sso(client, admin, mock_idp.issuer_url)
    query = await _start_login(client, admin["teamId"])
    mock_idp.pending_id_token = make_id_token(
        _claims(idp=mock_idp, nonce=query["nonce"], email="eng@x.com")
    )
    r = await _complete(client, admin["teamId"], query["state"], "c")
    assert r.status_code == 403
    assert _error_code(r) == "SSO_EMAIL_NOT_MEMBER"


# --------------------------------------------------------------------------
# Honest OTP fallback
# --------------------------------------------------------------------------


async def test_unconfigured_team_falls_back_to_otp_honestly(factory, client):
    admin = await _seed_team_admin(factory, plan=None)

    # No config at all: 404 SSO_NOT_CONFIGURED — the UI's signal to show OTP.
    r = await client.post(
        "/api/v1/auth/sso/start",
        json={"teamId": admin["teamId"], "redirectUri": REDIRECT_URI},
    )
    assert r.status_code == 404
    assert _error_code(r) == "SSO_NOT_CONFIGURED"

    # Disabled config behaves identically.
    async with factory() as db:
        db.add(
            TeamSsoConfig(
                team_id=admin["teamId"],
                issuer_url=ISSUER,
                client_id=CLIENT_ID,
                client_secret_encrypted="x" * 32,
                enabled=False,
            )
        )
        await db.commit()
    r = await client.post(
        "/api/v1/auth/sso/start",
        json={"teamId": admin["teamId"], "redirectUri": REDIRECT_URI},
    )
    assert r.status_code == 404
    assert _error_code(r) == "SSO_NOT_CONFIGURED"


async def test_otp_flow_unaffected_by_sso_lane(factory):
    """The parallel-door law: OTP keeps issuing + verifying codes untouched."""
    from fastapi import HTTPException

    sent: list[tuple[str, str]] = []

    class _Settings:
        otp_ttl_minutes = 10
        otp_max_attempts = 5

    await issue_otp(factory, _Settings(), lambda e, c: sent.append((e, c)), "Dev@X.com")
    (email, code) = sent[0]
    assert email == "dev@x.com" and len(code) == 6
    # the issued code verifies through the UNCHANGED verify path
    token = None
    try:
        token, principal = await verify_otp(factory(), _Settings(), "dev@x.com", code)
    except HTTPException:
        token = None
    assert token is not None
    assert principal.role.value == "admin"  # first login provisions a solo team


# --------------------------------------------------------------------------
# Migration parity + dependency law pins
# --------------------------------------------------------------------------


def test_migration_0033_matches_model_shape():
    sql = (
        Path(__file__).resolve().parents[1]
        .joinpath("migrations", "0033_team_sso.sql")
        .read_text()
    )
    assert "CREATE TABLE team_sso_configs" in sql
    assert "CREATE TABLE sso_login_states" in sql
    assert "REFERENCES teams (id) ON DELETE CASCADE" in sql
    cols = {c.name for c in TeamSsoConfig.__table__.columns}
    assert cols == {
        "team_id",
        "issuer_url",
        "client_id",
        "client_secret_encrypted",
        "enabled",
        "idp_display_name",
        "created_at",
    }
    state_cols = {c.name for c in SsoLoginState.__table__.columns}
    assert state_cols == {
        "state_hash",
        "nonce_hash",
        "pkce_verifier_hash",
        "team_id",
        "redirect_uri",
        "expires_at",
        "created_at",
    }


def test_no_vendor_sdk_imported():
    """Hard mission rule: raw OIDC only — no authlib/jose/pyjwt/okta/msal."""
    src = Path(__file__).resolve().parents[1].joinpath(
        "src", "harness_identity", "sso.py"
    ).read_text()
    for banned in ("authlib", "jose", "jwt", "okta", "msal", "requests_oauthlib"):
        assert f"import {banned}" not in src
        assert f"from {banned}" not in src
