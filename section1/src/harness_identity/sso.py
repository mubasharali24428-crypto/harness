"""Mission 22 M4 — OIDC discovery-based SSO hooks for the Team tier.

Team-tier customers bring their own identity. This module implements the
STANDARD OIDC flows with raw HTTP against the discovery document + JWKS —
no vendor SDK (hard mission rule; stdlib + httpx only):

  POST   /api/v1/auth/sso/start            -> {authorizationUrl}  (unauth)
  POST   /api/v1/auth/sso/complete         -> SessionResponse     (unauth)
  GET    /api/v1/teams/{id}/sso/config     -> config (hint-only secret)
  PUT    /api/v1/teams/{id}/sso/config     -> upsert (admin, Team tier)
  DELETE /api/v1/teams/{id}/sso/config                            (admin)

Flow (authorization code + PKCE, OIDC Core 1.0 §3.1 / RFC 7636):
 1. start: mint state + nonce + PKCE pair, persist HASHES in
    sso_login_states (UTC ISO-8601 TEXT — the migration-0005 discipline),
    fetch discovery {issuer, authorization_endpoint, token_endpoint,
    jwks_uri}, return the fully-built authorization URL.
 2. The IdP redirects back to the SPA; the frontend POSTs {teamId, code,
    state, redirectUri} here.
 3. complete: atomically consume the state row (DELETE ... RETURNING), swap
    the code at the token endpoint WITH the stored verifier, validate the ID
    token signature via JWKS fetched from the DISCOVERED jwks_uri (RS256,
    exp/iss/aud/nonce), then map to an existing member BY EMAIL:
      - email matches an active human member of THIS team -> session minted
        (AuthSession row; principal role = membership role).
      - no match -> 403 SSO_EMAIL_NOT_MEMBER. Provisioning is deliberately
        mapping-only: a stranger at the IdP can never create an account or
        join a team by logging in. Joining stays invite-only.
 4. OTP remains the default login path everywhere else — this module adds a
    parallel door and removes none.

Security posture:
- client_secret is Fernet-encrypted at rest under the salted KDF (crypto.py);
  reads project only a hint derived from ciphertext (no decrypt on GET).
- state/nonce/PKCE verifier are single-use, stored hashed/bounded-TTL;
  expired rows are swept opportunistically at mint time (ws_tickets pattern).
- redirect_uri is bound per attempt and must be echoed at complete time;
  any mismatch fails closed (SSO_STATE_INVALID).
- Team-tier gate on writes: PUT answers 403 PLAN_REQUIRED unless the team's
  billing plan is "team" (billing.BillingPlan) — SSO is the paid feature.
- No existence oracle: foreign team ids answer exactly what unknown ids do.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, error_body
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import ForeignKey, delete as sa_delete, select
from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from .audit_emit import emit_sso_config_deleted, emit_sso_config_saved
from .auth import SESSION_TTL_HOURS, get_principal, hash_token
from .billing import BillingPlan
from .crypto import ensure_kdf_params, load_kdf_params
from .models import AuthSession, Base, Role as DbRole, User

router = APIRouter(prefix="/api/v1")

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------

SSO_STATE_TTL_SECONDS = 600  # ten minutes to finish the round trip
HTTP_TIMEOUT_S = 10.0
ALLOWED_ID_TOKEN_ALGS = ("RS256",)  # OIDC Core mandates RS256; accept only that


class SsoError(Exception):
    """An SSO step failed for a reason safe to surface in an error body."""


# --------------------------------------------------------------------------
# ORM mirrors of migration 0033 (registered on the shared Base so scratch
# test apps pick them up via Base.metadata.create_all — billing.py pattern)
# --------------------------------------------------------------------------


class TeamSsoConfig(Base):
    """Mirror of 0033_team_sso.sql team_sso_configs."""

    __tablename__ = "team_sso_configs"

    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    issuer_url: Mapped[str] = mapped_column(nullable=False)
    client_id: Mapped[str] = mapped_column(nullable=False)
    # Fernet token under the KeyVault — NEVER plaintext (api_keys convention).
    client_secret_encrypted: Mapped[str] = mapped_column(nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    idp_display_name: Mapped[str] = mapped_column(default="", nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SsoLoginState(Base):
    """Mirror of 0033_team_sso.sql sso_login_states.

    Timestamps are UTC ISO-8601 TEXT (migration-0005 discipline): SQLite keeps
    DATETIME columns as TEXT and hand-seeded rows use isoformat(); storing
    pre-normalized text makes comparisons exact on both dialects.
    """

    __tablename__ = "sso_login_states"

    state_hash: Mapped[str] = mapped_column(primary_key=True)
    nonce_hash: Mapped[str] = mapped_column(nullable=False)
    pkce_verifier_hash: Mapped[str] = mapped_column(nullable=False)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    redirect_uri: Mapped[str] = mapped_column(nullable=False)
    expires_at: Mapped[str] = mapped_column(nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(nullable=False)


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _iso_in(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


# --------------------------------------------------------------------------
# PKCE (RFC 7636) — S256
# --------------------------------------------------------------------------


def generate_pkce_pair() -> tuple[str, str]:
    """Return (verifier, challenge). Verifier: 43+ chars of base64url."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


# --------------------------------------------------------------------------
# Discovery (OpenID Connect Discovery 1.0)
# --------------------------------------------------------------------------

REQUIRED_DISCOVERY_KEYS = (
    "issuer",
    "authorization_endpoint",
    "token_endpoint",
    "jwks_uri",
)


async def fetch_discovery(client: httpx.AsyncClient, issuer_url: str) -> dict[str, Any]:
    """GET <issuer>/.well-known/openid-configuration and validate its shape.

    Raises SsoError (safe message) when unreachable or malformed. The
    discovered issuer MUST equal the configured URL (OIDC Discovery §4.3);
    otherwise a compromised discovery endpoint could re-point token/JWKS URLs.
    """
    url = issuer_url.rstrip("/") + "/.well-known/openid-configuration"
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        doc = resp.json()
    except Exception as exc:  # noqa: BLE001 — surfaced as one safe error below
        raise SsoError(f"discovery document unreachable at {url}") from exc
    if not isinstance(doc, dict):
        raise SsoError("discovery document is not an object")
    missing = [k for k in REQUIRED_DISCOVERY_KEYS if not doc.get(k)]
    if missing:
        raise SsoError(f"discovery document missing keys: {', '.join(missing)}")
    if str(doc["issuer"]).rstrip("/") != issuer_url.rstrip("/"):
        raise SsoError("discovery issuer mismatch")
    return doc


async def fetch_jwks(client: httpx.AsyncClient, jwks_uri: str) -> dict[str, Any]:
    """GET the JWKS document; must contain at least one key entry."""
    try:
        resp = await client.get(jwks_uri)
        resp.raise_for_status()
        jwks = resp.json()
    except Exception as exc:  # noqa: BLE001 — one safe error below
        raise SsoError("JWKS unreachable") from exc
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not isinstance(keys, list) or not keys:
        raise SsoError("JWKS contains no keys")
    return jwks


# --------------------------------------------------------------------------
# ID-token validation — raw JWS per RFC 7515/7519, no jwt dependency
# --------------------------------------------------------------------------


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


# DigestInfo prefix for SHA-256 (RFC 8017 §9.2 note 1)
_DIGEST_INFO_SHA256 = bytes.fromhex("3031300d060960864801650304020105000420")


def _rsa_pkcs1v15_sha256_verify(signing_input: bytes, signature: bytes,
                                n: int, e: int) -> bool:
    """Raw RSASSA-PKCS1-v1_5 verify over SHA-256 (RFC 8017 §8.2.2).

    EM = 0x00 || 0x01 || PS(0xFF…) || 0x00 || DigestInfo(SHA-256). Returns
    False — never raises — on every verification failure shape.
    """
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    digest_info = _DIGEST_INFO_SHA256 + hashlib.sha256(signing_input).digest()
    expected = b"\x00\x01" + b"\xff" * (k - len(digest_info) - 3) + b"\x00" + digest_info
    return em == expected


def _select_rsa_jwk(jwks: dict[str, Any], kid: str | None) -> dict[str, Any] | None:
    """Pick the JWK matching ``kid`` — or the sole RSA key when omitted."""
    candidates = [
        k
        for k in jwks.get("keys", [])
        if isinstance(k, dict)
        and k.get("kty") == "RSA"
        and isinstance(k.get("n"), str)
        and isinstance(k.get("e"), str)
        and k.get("alg") in (None, *ALLOWED_ID_TOKEN_ALGS)
        and k.get("use") in (None, "sig")
    ]
    if kid is not None:
        for key in candidates:
            if key.get("kid") == kid:
                return key
        return None
    return candidates[0] if len(candidates) == 1 else None


def validate_id_token(id_token: str, jwks: dict[str, Any], *, issuer: str, audience: str,
                      nonce: str | None, now: datetime | None = None) -> dict[str, Any]:
    """Validate an ID token end-to-end and return its claims.

    Checks (fail-closed, in order): compact-JWS shape, RS256 alg header,
    signature over the signing input via the JWKS key, exp (60s leeway),
    iss equals the DISCOVERED issuer, aud contains client_id, and nonce
    binding when provided. Claims come back only after every check passed.
    """
    parts = id_token.split(".")
    if len(parts) != 3:
        raise SsoError("malformed ID token")
    header_b64, payload_b64, sig_b64 = parts
    try:
        header = json_loads(_b64url_decode(header_b64))
        claims = json_loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(sig_b64)
    except Exception as exc:  # noqa: BLE001 — malformed input is one safe error
        raise SsoError("malformed ID token") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise SsoError("malformed ID token")
    if header.get("alg") not in ALLOWED_ID_TOKEN_ALGS:
        raise SsoError("ID token algorithm rejected")
    jwk = _select_rsa_jwk(jwks, header.get("kid"))
    if jwk is None:
        raise SsoError("no matching signing key")

    n = int.from_bytes(_b64url_decode(jwk["n"]), "big")
    e = int.from_bytes(_b64url_decode(jwk["e"]), "big")
    if not _rsa_pkcs1v15_sha256_verify(
        f"{header_b64}.{payload_b64}".encode(), signature, n, e
    ):
        raise SsoError("ID token signature invalid")

    now = now or datetime.now(UTC)
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or now.timestamp() > float(exp) + 60:
        raise SsoError("ID token expired")
    if str(claims.get("iss", "")).rstrip("/") != issuer.rstrip("/"):
        raise SsoError("ID token issuer mismatch")
    aud = claims.get("aud")
    aud_values = [aud] if isinstance(aud, str) else (aud if isinstance(aud, list) else [])
    if audience not in aud_values:
        raise SsoError("ID token audience mismatch")
    if nonce is not None and claims.get("nonce") != nonce:
        raise SsoError("ID token nonce mismatch")
    return claims


def json_loads(raw: bytes) -> Any:
    import json

    return json.loads(raw)


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _role_value(role: object) -> str:
    return getattr(role, "value", str(role))


def _require_admin(principal: Principal) -> None:
    if _role_value(principal.role) != "admin":
        raise HTTPException(status_code=403, detail=error_body("FORBIDDEN", "admin only"))


async def _require_team_tier(request: Request, team_id: str) -> None:
    """SSO is a Team-tier feature: the team's billing plan must be 'team'.

    When billing storage cannot be consulted we degrade HONESTLY to refused
    (fail-closed for a paid-feature gate) rather than silently enabling it.
    """
    factory = request.app.state.session_factory
    plan_value: str | None = None
    try:
        async with factory() as db:
            plan_row = (
                await db.execute(select(BillingPlan).where(BillingPlan.team_id == team_id))
            ).scalar_one_or_none()
            plan_value = plan_row.plan if plan_row is not None else None
    except Exception:  # noqa: BLE001 — gate must never 500 on storage trouble
        plan_value = None
    if plan_value != "team":
        raise HTTPException(
            status_code=403,
            detail=error_body("PLAN_REQUIRED", "SSO requires the Team tier"),
        )


# --------------------------------------------------------------------------
# Config CRUD (admin-gated, Team-tier gated)
# --------------------------------------------------------------------------


def _secret_hint(encrypted_token: str) -> str:
    """Hint derived WITHOUT decrypting: last four chars of the Fernet token.

    Fernet tokens are high-entropy ciphertext, so these characters carry zero
    plaintext information — they exist so admins can see a secret IS set (and
    distinguish re-uploads). The read path never decrypts.
    """
    return "…" + encrypted_token[-4:] if len(encrypted_token) >= 4 else "…"


def _config_public(row: TeamSsoConfig) -> dict[str, Any]:
    """camelCase projection. Only the ciphertext hint of the secret leaves."""
    return {
        "teamId": row.team_id,
        "issuerUrl": row.issuer_url,
        "clientId": row.client_id,
        "enabled": bool(row.enabled),
        "idpDisplayName": row.idp_display_name,
        "secretHint": _secret_hint(row.client_secret_encrypted),
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


class SsoConfigBody(BaseModel):
    issuer_url: str = Field(alias="issuerUrl", min_length=8, max_length=500)
    client_id: str = Field(alias="clientId", min_length=1, max_length=256)
    # Optional on PUT: absent => keep the existing stored secret (update flow).
    client_secret: str | None = Field(
        alias="clientSecret", default=None, min_length=1, max_length=512
    )
    enabled: bool = True
    idp_display_name: str = Field(alias="idpDisplayName", default="", max_length=200)

    model_config = {"populate_by_name": True}

    @field_validator("issuer_url", "client_id", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


@router.get("/teams/{team_id}/sso/config")
async def get_sso_config(
    team_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    """Read the team's SSO config (any member; secret is hint-only)."""
    if principal.teamId != team_id:
        raise HTTPException(status_code=404, detail=error_body("NOT_FOUND", "team not found"))
    async with request.app.state.session_factory() as db:
        row = await db.get(TeamSsoConfig, team_id)
    if row is None:
        return {"teamId": team_id, "configured": False}
    out = _config_public(row)
    out["configured"] = True
    return out


@router.put("/teams/{team_id}/sso/config")
async def put_sso_config(
    team_id: str,
    body: SsoConfigBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    """Upsert the team's IdP configuration (admin + Team tier).

    The client secret is Fernet-encrypted under the salted KDF before storage
    and never returned. Omitting clientSecret on an update keeps the stored one.
    """
    _require_admin(principal)
    if principal.teamId != team_id:
        raise HTTPException(status_code=404, detail=error_body("NOT_FOUND", "team not found"))
    await _require_team_tier(request, team_id)

    vault = request.app.state.vault
    async with request.app.state.session_factory() as db:
        row = await db.get(TeamSsoConfig, team_id)
        created = row is None
        if row is None:
            if body.client_secret is None:
                raise HTTPException(
                    status_code=422,
                    detail=error_body("VALIDATION_ERROR", "clientSecret required on create"),
                )
            row = TeamSsoConfig(team_id=team_id)
            db.add(row)
        _, fernet = await ensure_kdf_params(db, vault.master_key)
        if body.client_secret is not None:
            row.client_secret_encrypted = fernet.encrypt(body.client_secret.encode()).decode()
        row.issuer_url = body.issuer_url.rstrip("/")
        row.client_id = body.client_id
        row.enabled = body.enabled
        row.idp_display_name = body.idp_display_name
        if row.created_at is None:
            row.created_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(row)
        # C-002 audit AFTER commit; detail carries booleans only — the secret
        # itself could not enter the row anyway (_scrub drops secret-shaped keys).
        await emit_sso_config_saved(
            db,
            actor_user_id=principal.userId,
            team_id=principal.teamId,
            target_id=team_id,
            detail={"created": created},
        )
        out = _config_public(row)
        out["configured"] = True
        return out


@router.delete("/teams/{team_id}/sso/config", status_code=204)
async def delete_sso_config(
    team_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    """Remove the config entirely; the team falls back to OTP. Idempotent."""
    _require_admin(principal)
    if principal.teamId != team_id:
        raise HTTPException(status_code=404, detail=error_body("NOT_FOUND", "team not found"))
    async with request.app.state.session_factory() as db:
        row = await db.get(TeamSsoConfig, team_id)
        had_row = row is not None
        if had_row:
            await db.delete(row)
            await db.commit()
            await emit_sso_config_deleted(
                db,
                actor_user_id=principal.userId,
                team_id=principal.teamId,
                target_id=team_id,
            )
    # Absent row: fall through — DELETE stays 204 (idempotent).


# --------------------------------------------------------------------------
# Login flow: start + complete (unauthenticated by design — it IS the login)
# --------------------------------------------------------------------------


class SsoStartBody(BaseModel):
    team_id: str = Field(alias="teamId", min_length=1, max_length=64)
    redirect_uri: str = Field(alias="redirectUri", min_length=1, max_length=500)

    model_config = {"populate_by_name": True}

    @field_validator("redirect_uri", mode="before")
    @classmethod
    def _strip_redirect(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


def _decryptable(*fernets, encrypted: str) -> bool:
    """True when at least one dual-read fernet can decrypt ``encrypted``."""
    for fernet in fernets:
        try:
            fernet.decrypt(encrypted.encode())
            return True
        except (ValueError, InvalidToken):
            continue
    return False


@router.post("/auth/sso/start")
async def sso_start(body: SsoStartBody, request: Request) -> dict[str, Any]:
    """Mint state/PKCE, persist hashes, build the authorization URL.

    Unknown/unconfigured/disabled teams answer 404 SSO_NOT_CONFIGURED so the
    UI falls back to plain OTP honestly — no probing oracle either way.
    """
    factory = request.app.state.session_factory
    async with factory() as db:
        row = await db.get(TeamSsoConfig, body.team_id)
        if row is None or not row.enabled:
            raise HTTPException(
                status_code=404,
                detail=error_body(
                    "SSO_NOT_CONFIGURED", "this team has no active SSO configuration"
                ),
            )
        issuer_url, client_id = row.issuer_url, row.client_id
        # Decrypt once here to prove readability early (503 before redirecting
        # the user's browser at a doomed flow); the secret is NOT sent anywhere
        # from this endpoint.
        _, new_fernet, legacy_fernet = await load_kdf_params(db, request.app.state.vault.master_key)
        if not await _decryptable(
            new_fernet, legacy_fernet, encrypted=row.client_secret_encrypted
        ):
            raise HTTPException(
                status_code=503,
                detail=error_body(
                    "SSO_SECRET_UNREADABLE", "stored IdP secret could not be decrypted"
                ),
            )

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as http:
            discovery = await fetch_discovery(http, issuer_url)

        verifier, challenge = generate_pkce_pair()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(24)
        db.add(
            SsoLoginState(
                state_hash=_sha256_hex(state),
                nonce_hash=_sha256_hex(nonce),
                pkce_verifier_hash=verifier,
                team_id=body.team_id,
                redirect_uri=body.redirect_uri.strip(),
                expires_at=_iso_in(SSO_STATE_TTL_SECONDS),
                created_at=_now_iso(),
            )
        )
        # Opportunistic sweep (ws_tickets pattern): expired states are inert.
        await db.execute(sa_delete(SsoLoginState).where(SsoLoginState.expires_at < _now_iso()))
        await db.commit()

    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": body.redirect_uri.strip(),
            "scope": "openid profile email",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return {"authorizationUrl": f"{discovery['authorization_endpoint']}?{query}"}


class SsoCompleteBody(BaseModel):
    team_id: str = Field(alias="teamId", min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=512)
    state: str = Field(min_length=16, max_length=256)
    redirect_uri: str = Field(alias="redirectUri", min_length=1, max_length=500)

    model_config = {"populate_by_name": True}


async def _consume_state(factory, state: str) -> dict[str, Any]:
    """Atomically redeem the state row: single-statement DELETE ... RETURNING
    (cross-process single-use, invites.py claim pattern)."""
    async with factory() as db:
        row = (
            await db.execute(
                sa_delete(SsoLoginState)
                .where(
                    SsoLoginState.state_hash == _sha256_hex(state),
                    SsoLoginState.expires_at > _now_iso(),
                )
                .returning(SsoLoginState.__table__)
                .execution_options(synchronize_session=False)
            )
        ).mappings().first()
        await db.commit()
    if row is None:
        raise HTTPException(
            status_code=400,
            detail=error_body("SSO_STATE_INVALID", "unknown, expired or replayed state"),
        )
    return dict(row)


@router.post("/auth/sso/complete")
async def sso_complete(body: SsoCompleteBody, request: Request) -> dict[str, Any]:
    """Swap the code, validate the ID token against JWKS, map to the member."""
    factory = request.app.state.session_factory
    state_row = await _consume_state(factory, body.state)
    if state_row["team_id"] != body.team_id or state_row["redirect_uri"] != body.redirect_uri.strip():
        raise HTTPException(
            status_code=400,
            detail=error_body("SSO_STATE_INVALID", "state does not match this login attempt"),
        )

    async with factory() as db:
        config = await db.get(TeamSsoConfig, body.team_id)
        if config is None:
            raise HTTPException(
                status_code=404,
                detail=error_body("SSO_NOT_CONFIGURED", "this team has no SSO configuration"),
            )
        _, new_fernet, legacy_fernet = await load_kdf_params(db, request.app.state.vault.master_key)
        client_secret: str | None = None
        for fernet in (new_fernet, legacy_fernet):
            try:
                # fernet.decrypt returns bytes; decode to a clean str.
                client_secret = fernet.decrypt(config.client_secret_encrypted.encode()).decode()
                break
            except (ValueError, InvalidToken):
                continue
        issuer_url, client_id = config.issuer_url, config.client_id

    if client_secret is None:
        raise HTTPException(
            status_code=503,
            detail=error_body(
                "SSO_SECRET_UNREADABLE", "stored IdP secret could not be decrypted"
            ),
        )

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as http:
        discovery = await fetch_discovery(http, issuer_url)
        jwks = await fetch_jwks(http, discovery["jwks_uri"])
        try:
            token_resp = await http.post(
                discovery["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": body.code,
                    "redirect_uri": body.redirect_uri.strip(),
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code_verifier": state_row["pkce_verifier_hash"],
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:  # noqa: BLE001 — network trouble is one honest 502
            raise HTTPException(
                status_code=502,
                detail=error_body("SSO_IDP_UNREACHABLE", "identity provider unreachable"),
            ) from exc
    if token_resp.status_code != 200:
        raise HTTPException(
            status_code=401,
            detail=error_body("SSO_TOKEN_EXCHANGE_FAILED", "authorization code rejected by IdP"),
        )
    tokens = token_resp.json()
    id_token = tokens.get("id_token") if isinstance(tokens, dict) else None
    if not isinstance(id_token, str) or not id_token:
        raise HTTPException(
            status_code=401,
            detail=error_body("SSO_TOKEN_EXCHANGE_FAILED", "token response carried no ID token"),
        )
    claims = validate_id_token(
        id_token,
        jwks,
        issuer=issuer_url,
        audience=client_id,
        nonce=state_row["nonce_hash"],
    )
    email = str(claims.get("email") or "").lower().strip()
    if not email:
        raise HTTPException(
            status_code=403,
            detail=error_body("SSO_EMAIL_MISSING", "ID token carries no email claim"),
        )

    principal = await _map_email_to_member(factory, body.team_id, email)
    if principal is None:
        raise HTTPException(
            status_code=403,
            detail=error_body(
                "SSO_EMAIL_NOT_MEMBER",
                "this account is not a member of the team; ask an admin for an invite",
            ),
        )
    token = await _mint_session(factory, principal)
    return {"token": token, "principal": principal.model_dump()}


async def _map_email_to_member(factory, team_id: str, email: str) -> Principal | None:
    """Email-match provisioning rule (the documented contract):

    The ID-token email must equal (case-insensitive, stripped) users.email of
    an EXISTING member OF THIS TEAM. Match -> Principal carrying that
    membership's role. No match -> None (caller answers 403; nothing is
    provisioned). Strangers at the IdP gain nothing — no user row, no team
    seat, no session. Joining remains the audited invite-token flow.
    """
    async with factory() as db:
        user = (
            await db.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if user is None:
            return None
        from .models import TeamMember

        member = (
            await db.execute(
                select(TeamMember).where(
                    TeamMember.team_id == team_id,
                    TeamMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if member is None:
            return None
        return Principal(
            userId=user.id, teamId=team_id, role=_role_value_to_contracts(member.role)
        )


def _role_value_to_contracts(role: object):
    """models.Role (StrEnum) -> contracts.Role, by VALUE (teams_api convention)."""
    from harness_contracts.roles import Role as ContractsRole

    return ContractsRole(_role_value(role))


async def _mint_session(factory, principal: Principal) -> str:
    """Persist an AuthSession and return the raw bearer token (auth.py scheme)."""
    raw = secrets.token_urlsafe(32)
    async with factory() as db:
        db.add(
            AuthSession(
                token_hash=hash_token(raw),
                user_id=principal.userId,
                team_id=principal.teamId,
                expires_at=datetime.now(UTC) + timedelta(hours=SESSION_TTL_HOURS),
            )
        )
        await db.commit()
    return raw
