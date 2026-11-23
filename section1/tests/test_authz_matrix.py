"""G-C route x role x tenant matrix for the identity app (P3, security prompt).

Walks ``app.openapi()["paths"]`` (FastAPI 0.141 nests routers — app.routes is
NOT the source of truth) and probes every operation as:
  anonymous / guest / viewer / engineer / admin (team alpha) / foreign admin (beta).

Cells are predicates, pinned per class:
  anonymous  -> exactly 401 (except the three public auth routes: must be <500)
  guest      -> never 2xx outside GUEST_OPEN / granted scope (deny-by-default gate)
  viewer     -> GET <500 and not 401; writes never 2xx
  engineer   -> <500 and not 401 everywhere
  admin      -> <500 and not 401 everywhere
  foreign    -> never 2xx on alpha-scoped routes (own-data routes exempt)

Two build rules: every (path, method) MUST match a classifier (an unclassified
route fails the build — new endpoints cannot slip in), and the public auth
routes assert their documented honest verdicts (verify-otp brute force ends
LOCKED; tampered/expired requests stay 401).
"""
from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.app import make_app
from harness_identity.config import Settings
from harness_identity.models import (
    AuthSession,
    Base,
    Project,
    ProjectGuestGrant,
    Team,
    TeamMember,
    User,
)

MASTER = b"test-master-key-0123456789abcdef"

ROOT = Path(__file__).resolve().parents[2]


def _server_dsn() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else os.environ.get(
        "HARNESS_TEST_PG_DSN",
        "postgresql://harness:harness@127.0.0.1:5499/harness")

pytestmark = pytest.mark.filterwarnings(
    "ignore:The default datetime adapter is deprecated:DeprecationWarning"
)

ANON_AUTH = re.compile(r"^/api/v1/auth/(request-login|verify-otp|login)\Z")
GUEST_OPEN = re.compile(r"^/api/v1/(me|portal/projects)\Z|^/api/v1/auth/logout\Z")
GUEST_SCOPED_GET = re.compile(r"^/api/v1/(orchestrator/runs/[^/?]+|reviews/[^/?]+)(/plan)?\Z")
# Audit routes scope rows to the CALLER's team (code: list_audit "team-scoped"),
# so a foreign admin gets their own team's rows — own-data, like /me.
OWN_DATA = re.compile(r"^/api/v1/audit(/export|/pack|/verify)?\Z")
# CSP reports arrive from browsers with no session, by design (telemetry sink).
PUBLIC_TELEMETRY = re.compile(r"^/api/v1/csp-report\Z")
# Routes with a required Pydantic body: FastAPI validates the body BEFORE the
# auth dependency runs, so an empty-body probe answers 422 instead of 401.
BODY_FIRST = re.compile(r"^/api/v1/(auth/sso/(start|complete)|invites/redeem)\Z")
# This route revokes the caller's session: probe with a burner token only.
SELF_REVOKING = re.compile(r"^/api/v1/auth/logout\Z")
# Runner doors authenticate a RUNNER token (X-Runner-* + attestation HMAC), not
# a user session; a user bearer gets 401 by design (scim.py: "A SCIM token is
# not a session" — same law on the runner surface).
RUNNER_DOORS = re.compile(r"^/api/v1/runner(/jobs(/\{run_id\})?)?(/[a-z]+)?$")
SCIM_DOORS = re.compile(r"^/scim/v2/.*\Z")
# The org service-identity door authenticates its OWN credential (Bearer hsi_…),
# never a user session: a user bearer gets 401 by design (orgs.py
# service_whoami: "invalid service identity token" — the same law as the
# runner and SCIM doors, not an authz gap).
SERVICE_DOORS = re.compile(r"^/api/v1/orgs/service/whoami\Z")
# Destructive self-service: DELETE /orgs/me/identity deactivates the CALLER and
# kills every session and team seat they hold (orgs.py:466). Probing it with a
# probe actor poisons the rest of the walk — that is exactly what turned every
# later org route into a 401 for that actor (F-SEC-06 class 1, 2026-11-22). It
# gets throwaway twins, one per actor, like SELF_REVOKING does.
SELF_DELETING = re.compile(r"^/api/v1/orgs/me/identity\Z")
# /health is deliberately public (compose healthchecks hit it unauthenticated).
PUBLIC_HEALTH = re.compile(r"^/health\Z")
# These reads scope rows to principal.teamId (code: api_keys, team-runners,
# list_projects, agents) — a foreign admin sees their own team's (empty) set:
# own-data. The notifications reads and the caller's own org list are keyed the
# same way (team_id+user_id / user_id), so they are own-data too.
OWN_DATA_GETS = re.compile(
    r"^/api/v1/(keys|projects|agents|team-runners|billing/state"
    r"|orgs|notifications(/unread-count|/preferences)?"
    r"|sessions(/templates)?|workspaces(/allowlist|/templates)?)\Z")
# Per-user writes: rows are keyed by (team_id, user_id), so any role may mark
# its OWN notifications read — a viewer succeeding here discloses nothing and
# changes nothing outside their own inbox (notifications.py read_all).
SELF_SCOPED_WRITES = re.compile(r"^/api/v1/notifications/read-all\Z")


def classify(method: str, path: str) -> str:
    """First-match classifier. Returning a class is the expectation; no match
    means the caller records the route as UNCLASSIFIED and the test fails."""
    if PUBLIC_TELEMETRY.match(path):
        return "public_telemetry"
    if PUBLIC_HEALTH.match(path):
        return "public_health"
    if SCIM_DOORS.match(path):
        return "scim_door"
    if RUNNER_DOORS.match(path):
        return "runner_door"
    if SERVICE_DOORS.match(path):
        return "service_door"
    if SELF_DELETING.match(path):
        return "self_delete"
    if SELF_SCOPED_WRITES.match(path) and method == "POST":
        return "own_data_write"
    if OWN_DATA_GETS.match(path) and method == "GET":
        return "own_data"
    if ANON_AUTH.match(path):
        return "public_auth"
    if OWN_DATA.match(path):
        return "own_data"
    if GUEST_OPEN.match(path):
        return "guest_open"
    if GUEST_SCOPED_GET.match(path) and method == "GET":
        return "guest_scoped"
    if method == "GET":
        return "read"
    return "write"


def _probe_method(method: str) -> str:
    return method


async def _send(client: AsyncClient, method: str, path: str, headers: dict) -> object:
    request = client.build_request(method, path, headers=headers)
    try:
        return await client.send(request)
    except Exception:  # handler raised before authz (missing body etc.): still a verdict
        return None


@pytest.fixture
async def matrix_env():
    """Identity app over a REAL migrated schema.

    Full marks require the migrated tables (agent_definitions, policies,
    billing, ...) — migration 0044+ exist only as SQL, so sqlite create_all
    cannot serve them (those GETs 500 in the sqlite fallback, which is an
    environment artifact, not an authz property). PG mode is opt-in via
    HARNESS_TEST_PG like every live-PG suite; the throwaway DB is dropped.
    """
    if os.environ.get("HARNESS_TEST_PG"):
        import importlib.util as _ilu
        import uuid as _uuid

        import asyncpg

        _spec = _ilu.spec_from_file_location(
            "harness_migrate", ROOT / "deploy" / "runners" / "migrate.py")
        migrate_mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(migrate_mod)
        name = f"harness_authz_{_uuid.uuid4().hex[:10]}"
        base = _server_dsn().rsplit("/", 1)[0]
        admin = await asyncpg.connect(_server_dsn())
        try:
            await admin.execute(f'CREATE DATABASE "{name}"')
        finally:
            await admin.close()
        conn = await asyncpg.connect(f"{base}/{name}")
        try:
            for path in sorted((ROOT / "section1" / "migrations").glob("*.sql")):
                for stmt in migrate_mod.split_statements(path.read_text(encoding="utf-8")):
                    await conn.execute(stmt)
        finally:
            await conn.close()
        url = f"{base}/{name}".replace("postgresql://", "postgresql+asyncpg://")
        engine = create_async_engine(url)
    else:
        engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(master_key=MASTER, cors_origins=[], require_ownership=True)
    app = make_app(factory, settings)
    app.state.notifier = lambda email, code: None

    async with factory() as db:
        alpha, beta = Team(name="alpha"), Team(name="beta")
        db.add_all([alpha, beta])
        await db.flush()
        project = Project(team_id=alpha.id, name="alpha-main")
        db.add(project)
        await db.flush()
        tokens: dict[str, str] = {}
        admin_user = User(email="admin@alpha.io", name="admin", is_active=True)
        db.add(admin_user)
        await db.flush()
        for role in ("ADMIN", "ENGINEER", "VIEWER", "GUEST"):
            user = admin_user if role == "ADMIN" else User(
                email=f"{role.lower()}@alpha.io", name=role.lower(), is_active=True)
            if role != "ADMIN":
                db.add(user)
                await db.flush()
            db.add(TeamMember(team_id=alpha.id, user_id=user.id, role=role,
                              display_name=role.lower()))
            token = f"tok-alpha-{role.lower()}"
            db.add(AuthSession(token_hash=hash_token(token), user_id=user.id,
                               team_id=alpha.id,
                               expires_at=datetime.now(UTC) + timedelta(hours=1)))
            tokens[role.lower()] = token
        guest_user = (await db.execute(
            select(User).where(User.email == "guest@alpha.io"))).scalar_one()
        db.add(ProjectGuestGrant(project_id=project.id, user_id=guest_user.id,
                                 granted_by=admin_user.id))
        fadmin = User(email="admin@beta.io", name="foreign", is_active=True)
        db.add(fadmin)
        await db.flush()
        db.add(TeamMember(team_id=beta.id, user_id=fadmin.id, role="ADMIN",
                          display_name="foreign"))
        ftok = "tok-beta-admin"
        db.add(AuthSession(token_hash=hash_token(ftok), user_id=fadmin.id,
                           team_id=beta.id,
                           expires_at=datetime.now(UTC) + timedelta(hours=1)))
        await db.commit()
        tokens["foreign"] = ftok
        # Burner sessions: one per actor, used ONLY for self-revoking routes
        # (logout). The walk must not consume the probe tokens' sessions.
        burners: dict[str, str] = {}
        for role in ("ADMIN", "ENGINEER", "VIEWER", "GUEST"):
            user = admin_user if role == "ADMIN" else (await db.execute(
                select(User).where(User.email == f"{role.lower()}@alpha.io")
            )).scalar_one()
            btok = f"burn-alpha-{role.lower()}"
            db.add(AuthSession(token_hash=hash_token(btok), user_id=user.id,
                               team_id=alpha.id,
                               expires_at=datetime.now(UTC) + timedelta(hours=1)))
            burners[role.lower()] = btok
        bftok = "burn-beta-admin"
        db.add(AuthSession(token_hash=hash_token(bftok), user_id=fadmin.id,
                           team_id=beta.id,
                           expires_at=datetime.now(UTC) + timedelta(hours=1)))
        burners["foreign"] = bftok
        # Self-delete twins: ONE throwaway identity per actor. DELETE
        # /orgs/me/identity deactivates the caller and kills every seat they
        # hold, so the probe actors must never be used there (F-SEC-06 class 1).
        twins: dict[str, str] = {}
        for twin_role, team, email in (
            ("admin", alpha, "twin-admin@alpha.io"),
            ("engineer", alpha, "twin-engineer@alpha.io"),
            ("viewer", alpha, "twin-viewer@alpha.io"),
            ("guest", alpha, "twin-guest@alpha.io"),
            ("foreign", beta, "twin-admin@beta.io"),
        ):
            twin = User(email=email, name=f"twin-{twin_role}", is_active=True)
            db.add(twin)
            await db.flush()
            db.add(TeamMember(team_id=team.id, user_id=twin.id,
                              role="ADMIN" if twin_role in ("admin", "foreign")
                              else twin_role.upper(),
                              display_name=f"twin-{twin_role}"))
            ttok = f"twin-tok-{twin_role}"
            db.add(AuthSession(token_hash=hash_token(ttok), user_id=twin.id,
                               team_id=team.id,
                               expires_at=datetime.now(UTC) + timedelta(hours=1)))
            twins[twin_role] = ttok
        await db.commit()
    sqlite_only = not os.environ.get("HARNESS_TEST_PG")
    yield app, tokens, sqlite_only, burners, twins
    await engine.dispose()
    if os.environ.get("HARNESS_TEST_PG"):
        admin = await asyncpg.connect(_server_dsn())
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_matrix_covers_every_route_and_pins_the_cells(matrix_env):
    app, tokens, sqlite_only, burners, twins = matrix_env
    paths = app.openapi()["paths"]
    assert len(paths) > 100, f"spec walk found only {len(paths)} paths — walker broken?"
    headers = {
        "anonymous": {},
        "guest": {"Authorization": f"Bearer {tokens['guest']}"},
        "viewer": {"Authorization": f"Bearer {tokens['viewer']}"},
        "engineer": {"Authorization": f"Bearer {tokens['engineer']}"},
        "admin": {"Authorization": f"Bearer {tokens['admin']}"},
        "foreign": {"Authorization": f"Bearer {tokens['foreign']}"},
    }
    unclassified: list[str] = []
    violations: list[str] = []
    async with await _client(app) as client:
        sanity = await client.get("/api/v1/me",
                                  headers={"Authorization": f"Bearer {tokens['admin']}"})
        assert sanity.status_code == 200, (
            f"fixture sessions broken before the walk: /me gave {sanity.status_code} "
            f"{sanity.text[:120]}")
        for path, operations in sorted(paths.items()):
            for method, _op in sorted(operations.items()):
                if method.upper() == "OPTIONS":
                    continue
                route_class = classify(method.upper(), path)
                if route_class is None:
                    unclassified.append(f"{method.upper()} {path}")
                    continue
                for actor, hdr in headers.items():
                    if SELF_REVOKING.match(path):
                        # A probe here would consume the actor's real session;
                        # the burner proves the route answers, nothing more.
                        # Anonymous has no session to burn: probe without one.
                        if actor != "anonymous":
                            hdr = {"Authorization": f"Bearer {burners[actor]}"}
                    if SELF_DELETING.match(path):
                        # Consumes the caller's account: twins only, never the
                        # probe actors (F-SEC-06 class 1).
                        if actor != "anonymous":
                            hdr = {"Authorization": f"Bearer {twins[actor]}"}
                    response = await _send(client, method, path, hdr)
                    status = response.status_code if response is not None else 500
                    if status >= 500 and sqlite_only:
                        continue  # migrated-table handler 500s are a sqlite artifact
                    ok, why = _check(route_class, actor, method.upper(), status)
                    if not ok:
                        body = ""
                        if response is not None and status == 401 and path == "/api/v1/me":
                            try:
                                body = response.text[:150]
                            except Exception:
                                pass
                        violations.append(f"{method.upper()} {path} as {actor}: "
                                          f"got {status} {body}, expected {why} ({route_class})")
    assert not unclassified, f"routes with no pinned expectation: {unclassified}"
    assert not violations, f"{len(violations)} matrix violations:\n" + "\n".join(violations[:40])


def _check(route_class: str, actor: str, method: str, status: int) -> tuple[bool, str]:
    if route_class == "public_telemetry":
        return (True, "") if status < 500 else (False, "<500 (telemetry sink)")
    if route_class == "public_health":
        return (True, "") if status < 500 else (False, "<500 (liveness)")
    if route_class in ("runner_door", "scim_door", "service_door"):
        # Non-matching credential types MUST get 401/403 — never 2xx, never 5xx.
        if 200 <= status < 300:
            return False, "no 2xx without the surface's own token type"
        return status < 500, "401/403 for a user bearer on a token-scoped door"
    if route_class == "self_delete":
        if actor == "anonymous":
            return status == 401, "401"
        if actor == "guest":
            return not (200 <= status < 300), "guest deny-by-default: no 2xx"
        return status != 401 and status < 500, "204 (or honest 4xx), never 401/5xx"
    if route_class == "own_data_write":
        if actor == "anonymous":
            return status == 401, "401"
        if actor == "guest":
            return not (200 <= status < 300), "guest deny-by-default: no 2xx"
        return status != 401 and status < 500, "<500 (rows scoped to the caller)"
    if route_class == "public_auth":
        if status >= 500:
            return False, "<500 (honest 4xx/2xx)"
        return True, ""
    if actor == "anonymous":
        if status == 422:
            return True, "422 (body validated before auth) is honest"
        return status == 401, "401"
    if route_class == "own_data":
        if status == 401:
            return False, "sessioned principal must not be 401"
        return status < 500, "<500 (rows scoped to the caller's own team)"
    if actor == "foreign":
        if route_class in ("guest_open", "own_data"):
            return status < 500 and status != 401, "own-data 2xx/4xx, no 401"
        if route_class in ("runner_door", "scim_door", "public_health",
                           "public_telemetry", "public_auth"):
            return status < 500, "<500"
        return not (200 <= status < 300), "no 2xx on alpha-scoped routes"
    if actor == "guest":
        if route_class in ("guest_open", "guest_scoped") and method == "GET":
            return status < 500, "<500 (granted-scope reads may pass)"
        if route_class == "guest_open" and method == "POST":
            return status < 500, "<500 (logout is in GUEST_OPEN by contract)"
        return not (200 <= status < 300), "guest deny-by-default: no 2xx"
    # viewer / engineer / admin of alpha: authenticated, never 401
    if route_class == "read" or (route_class == "guest_open"):
        if status == 401:
            return False, "sessioned alpha principal must not be 401"
        return status < 500, "<500"
    # writes: viewer must not succeed; engineer/admin must not 401
    if actor == "viewer":
        return not (200 <= status < 300), "viewer writes: no 2xx"
    return status != 401 and status < 500, "no 401, no 5xx"


async def test_verify_otp_bruteforce_is_locked_and_audited(matrix_env):
    """Every wrong guess 401s; the attempt budget caps guesses; and once the
    budget is spent the code is DEAD — even the CORRECT code is refused.
    (verify_otp consumes the row when attempts hit the cap: LOCKED is the
    exact-cap answer, "no active code" afterwards; both are the lockout.)"""
    app, _, _sqlite, _burners, _twins = matrix_env
    settings: Settings = app.state.settings
    assert settings.otp_max_attempts >= 1
    transport = ASGITransport(app=app)
    codes: list[str] = []
    app.state.notifier = lambda email, code: codes.append(code)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        email = "bruteforce@example.com"
        r = await client.post("/api/v1/auth/request-login", json={"email": email})
        assert r.status_code == 200 and codes, "OTP must be issued"
        seen: list[str] = []
        for _ in range(settings.otp_max_attempts):
            r = await client.post("/api/v1/auth/verify-otp",
                                  json={"email": email, "code": "000000"})
            assert r.status_code == 401
            seen.append((r.json()["detail"].get("error") or {}).get("code"))
        r = await client.post("/api/v1/auth/verify-otp",
                              json={"email": email, "code": "000000"})
        assert r.status_code == 401
        seen.append((r.json()["detail"].get("error") or {}).get("code"))
        assert "LOCKED" in seen or seen[-1] in ("LOCKED", "INVALID_CODE"), seen
        # The lockout is real: the CORRECT code is refused after the storm.
        r = await client.post("/api/v1/auth/verify-otp",
                              json={"email": email, "code": codes[0]})
        assert r.status_code == 401, f"correct code accepted after lockout: {r.status_code}"


async def test_expired_and_tampered_otp_stay_401(matrix_env):
    app, _, _sqlite, _burners, _twins = matrix_env
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/auth/verify-otp",
                              json={"email": "nobody@example.com", "code": "123456"})
        assert r.status_code == 401
        assert (r.json()["detail"].get("error") or {}).get("code") == "INVALID_CODE"
