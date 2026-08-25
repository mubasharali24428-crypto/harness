"""M12-G3 — platform-wide request-size caps (raw-06 #9 hardening sweep).

Every JSON request field that carries unbounded free text now dies at
Pydantic validation (FastAPI → 422) instead of reaching handlers/DB/logs.
The caps live in ``harness_identity.limits`` and are deliberately generous:
nothing a legitimate client sends today can trip them (additive-only change).

Covered here:

* over-limit → rejected with 422; where lane F1's envelope middleware is
  installed the body carries the frozen
  ``{"error": {"code": "VALIDATION_ERROR", ...}}`` shape;
* at-limit → accepted (the request passes validation and reaches the
  handler's normal semantics);
* parity — section1's shipped caps equal the flat PLATFORM_CAPS registry so
  all sections stay on one set of numbers.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from harness_identity.app import make_app
from harness_identity.config import Settings
from harness_identity.envelope_middleware import install_error_envelope
from harness_identity.limits import (
    LOGIN_EMAIL_MAX,
    LOGIN_TEAM_ID_MAX,
    LOGIN_TEAM_NAME_MAX,
    OTP_CODE_MAX,
    PLATFORM_CAPS,
    PROJECT_NAME_MAX,
    PROJECT_REPO_FULL_NAME_MAX,
    WS_TICKET_PROJECT_ID_MAX,
)

MASTER = b"test-master-key-0123456789abcdef"

# Parity guard against the sibling sections' mirrored cap modules (S4/S6 venvs
# do not install harness_identity, so they ship byte-equal copies of limits.py).
_ROOT = Path(__file__).resolve().parents[2]
_SHARED_LIMITS = _ROOT / "section1" / "src" / "harness_identity" / "limits.py"
_S4_LIMITS = _ROOT / "section4" / "src" / "harness_orchestrator" / "_limits.py"
_S6_LIMITS = _ROOT / "section6" / "src" / "harness_meeting" / "_limits.py"


# --------------------------------------------------------------- fixtures


@pytest.fixture
async def caps_app():
    """In-memory identity app (ownership on) + F1 error-envelope installed."""
    engine = create_async_engine("sqlite+aiosqlite://")
    from harness_identity.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(master_key=MASTER, cors_origins=[], require_ownership=True)
    app: FastAPI = make_app(factory, settings)
    try:
        yield app
    finally:
        await engine.dispose()


async def _bearer_token(app) -> str:
    """Prove inbox ownership through the real OTP flow → bearer token.

    Needed because FastAPI resolves auth dependencies BEFORE body validation:
    protected routes would answer 401 before the size caps could speak.
    """
    sent: list[tuple[str, str]] = []
    app.state.notifier = lambda email, code: sent.append((email, code))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        asked = await client.post("/api/v1/auth/request-login", json={"email": "g3@example.com"})
        assert asked.status_code == 200
        verified = await client.post(
            "/api/v1/auth/verify-otp", json={"email": "g3@example.com", "code": sent[0][1]}
        )
        assert verified.status_code == 200
    return verified.json()["token"]


# ---------------------------------------------------------------------------
# Over-limit → 422 (+ frozen envelope when F1 middleware is installed)
# ---------------------------------------------------------------------------


def _open_route_over_limit_cases():
    """Routes reachable WITHOUT a principal (auth dep would preempt 422)."""
    return [
        (
            "/api/v1/auth/request-login",
            {"email": "a" * (LOGIN_EMAIL_MAX - len("@x.co") + 1) + "@x.co"},
        ),
        (
            "/api/v1/auth/login",
            {"email": "ok@example.com", "teamName": "T" * (LOGIN_TEAM_NAME_MAX + 1)},
        ),
        (
            "/api/v1/auth/login",
            {"email": "ok@example.com", "teamId": "t" * (LOGIN_TEAM_ID_MAX + 1)},
        ),
        (
            "/api/v1/auth/verify-otp",
            {"email": "ok@example.com", "code": "1" * (OTP_CODE_MAX + 1)},
        ),
    ]


def _protected_route_over_limit_cases():
    """Routes behind get_principal — need a bearer token to reach validation."""
    return [
        ("/api/v1/projects", {"name": "p" * (PROJECT_NAME_MAX + 1)}),
        (
            "/api/v1/projects",
            {"name": "ok", "repoFullName": "o/" + "r" * PROJECT_REPO_FULL_NAME_MAX},
        ),
        ("/api/v1/ws-ticket", {"projectId": "z" * (WS_TICKET_PROJECT_ID_MAX + 1)}),
    ]


def test_over_limit_requests_rejected_422_with_envelope(caps_app):
    """Over-cap fields die at validation; F1 middleware gives the frozen body."""
    install_error_envelope(caps_app)
    client = TestClient(caps_app)
    headers = {"Authorization": f"Bearer {anyio_run(_bearer_token, caps_app)}"}
    cases = _open_route_over_limit_cases() + _protected_route_over_limit_cases()
    for path, payload in cases:
        resp = client.post(path, json=payload, headers=headers)
        assert resp.status_code == 422, f"{path} payload head={str(payload)[:60]}"
        body = resp.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert body["error"]["message"]
        # F1 keeps a mirrored legacy detail so pre-existing clients keep working.
        assert body["detail"]["error"]["code"] == "VALIDATION_ERROR"


def anyio_run(fn, *args):
    import asyncio

    return asyncio.run(fn(*args))


def test_over_limit_without_envelope_middleware_is_still_422(caps_app):
    """Sections that have not wired F1 yet still reject at 422 (loose shape)."""
    # NOTE: caps_app already has handlers installed by the previous test only
    # if fixtures were shared; fixtures are function-scoped, so this app is
    # clean of F1 handlers — install nothing here.
    client = TestClient(caps_app)
    resp = client.post(
        "/api/v1/auth/request-login",
        json={"email": "a" * (LOGIN_EMAIL_MAX - len("@x.co") + 1) + "@x.co"},
    )
    assert resp.status_code == 422
    body = resp.json()
    err = body.get("error")
    if isinstance(err, dict):
        assert err["code"] == "VALIDATION_ERROR"
    else:
        assert isinstance(body.get("detail"), list)  # bare FastAPI 422 payload


# ---------------------------------------------------------------------------
# At-limit → accepted (validation passes, handler semantics take over)
# ---------------------------------------------------------------------------


def test_at_limit_requests_pass_validation(caps_app):
    """Fields exactly AT the cap are never rejected by the size caps."""
    client = TestClient(caps_app)

    # email exactly at cap → passes validation; require_ownership answers
    # 403 OWNERSHIP_REQUIRED (handler logic), NOT 422.
    email = "a" * (LOGIN_EMAIL_MAX - len("@x.co")) + "@x.co"
    r = client.post("/api/v1/auth/login", json={"email": email})
    assert r.status_code == 403
    assert r.json()["detail"]["error"]["code"] == "OWNERSHIP_REQUIRED"

    # teamName exactly at cap alongside it → same handler-level 403.
    r = client.post(
        "/api/v1/auth/login", json={"email": email, "teamName": "T" * LOGIN_TEAM_NAME_MAX}
    )
    assert r.status_code == 403

    # project name exactly at cap → passes validation; auth gate answers
    # 401 (no token), NOT 422.
    r = client.post("/api/v1/projects", json={"name": "n" * PROJECT_NAME_MAX})
    assert r.status_code == 401

    # ws-ticket projectId exactly at cap → same: auth gate, not validation.
    r = client.post("/api/v1/ws-ticket", json={"projectId": "p" * WS_TICKET_PROJECT_ID_MAX})
    assert r.status_code == 401

    # verify-otp code exactly at cap must NOT be a 422 either way (the real
    # flow's outcome depends on OTP state, which is not this lane's concern).
    r = client.post(
        "/api/v1/auth/verify-otp",
        json={"email": "at-limit@example.com", "code": "0" * OTP_CODE_MAX},
    )
    assert r.status_code != 422


# ---------------------------------------------------------------------------
# Parity: one shared registry, mirrored copies pinned per section
# ---------------------------------------------------------------------------


def test_platform_caps_registry_shape():
    assert PLATFORM_CAPS["S1.LOGIN_EMAIL_MAX"] == 320
    assert PLATFORM_CAPS["S4.PROPOSE_GOAL_MAX"] == 20_000
    assert PLATFORM_CAPS["S4.HUMAN_EDIT_PATH_MAX"] == 512
    assert PLATFORM_CAPS["S4.HUMAN_EDIT_CONTENT_MAX"] == 100_000
    assert PLATFORM_CAPS["S4.DONE_DIFF_TEXT_MAX"] == 200_000
    assert PLATFORM_CAPS["S6.MESSAGE_CONTENT_MAX"] == 10_000
    assert PLATFORM_CAPS["S6.MEETING_TITLE_MAX"] == 200
    assert all(isinstance(v, int) and v > 0 for v in PLATFORM_CAPS.values())


def test_mirrored_section_limit_copies_match_registry():
    """S4/S6 limit shims stay in lockstep with the canonical registry.

    M17-B3 dedupe: the byte-equal ``_limits.py`` copies are gone; each
    section ships a thin re-export of its slice from the canonical frozen
    registry in ``harness_contracts.limits``. This test now pins THAT seam:
    every name a shim exports must exist in PLATFORM_CAPS and carry the same
    value, so a drive-by cap edit desyncing any section still fails CI.
    """
    import importlib.util
    import sys

    assert _S4_LIMITS.exists() and _S6_LIMITS.exists()
    contracts_limits = importlib.import_module("harness_contracts.limits")
    for mirror in (_S4_LIMITS, _S6_LIMITS):
        section_name = mirror.parent.parent.parent.name  # .../sectionN/src/pkg/_limits.py
        # Load the shim module under a synthetic name (the section packages
        # themselves are not installed into this venv).
        spec = importlib.util.spec_from_file_location(
            f"_mirror_parity_{section_name}", mirror
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        try:
            spec.loader.exec_module(mod)
            exported = getattr(mod, "__all__", [])
            assert exported, f"{mirror} re-exports nothing"
            for name in exported:
                value = getattr(mod, name)
                key = f"S{section_name.split('section', 1)[-1]}.{name}"
                assert key in PLATFORM_CAPS, f"{name} missing from PLATFORM_CAPS"
                assert PLATFORM_CAPS[key] == value, (
                    f"{mirror}: {name} drifted from {key}"
                )
                assert contracts_limits.__dict__[name] == value
        finally:
            sys.modules.pop(spec.name, None)
