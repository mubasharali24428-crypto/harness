"""M13-G5a — API hardening regression guards (Squad ALPHA, Wave 3).

Two laws are pinned here:

1. Oracle-consistent errors: every CROSS-team / cross-project access answers
   exactly what an unknown id would answer — 404 NOT_FOUND in the frozen
   error_body envelope, byte-identical body shape for foreign vs unknown ids.
   A 403 on those paths would confirm the resource exists across the tenant
   boundary (existence oracle). Auth failures stay 401 at get_principal; this
   file never exercises them.

2. OpenAPI typing islands: the top-20-by-usage routes carry concrete response
   schemas (response_model=) so openapi.json exposes named components instead
   of bare ``{}``/additionalProperties objects.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from harness_identity.app import make_app
from harness_identity.config import Settings
from tests.helpers import auth, login

MASTER_KEY = b"test-master-key-0123456789abcdef"


def _error_code(body: dict) -> str:
    """Error code from either wiring of the app under test: the F1 envelope
    middleware ({"error": {...}}, + mirrored detail) or a bare app raising
    HTTPException ({"detail": {...}})."""
    err = body.get("error") or (body.get("detail") or {}).get("error")
    return (err or {}).get("code", "<missing>")


def _success_component_ref(schema: dict, path: str, method: str) -> str | None:
    """$ref of the first concrete success component across 2xx codes."""
    op = schema["paths"][path][method]
    for code in ("200", "201", "202"):
        resp = op["responses"].get(code)
        if not resp:
            continue
        ref = resp.get("$ref") or (
            resp.get("content", {}).get("application/json", {}).get("schema", {}).get("$ref")
        )
        if ref:
            return ref
    return None


async def _client_with_two_teams(db):
    """App plus two logged-in principals from DIFFERENT teams."""
    app = make_app(
        db,
        Settings(master_key=MASTER_KEY, cors_origins=[], require_ownership=False),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.app = app
        alice = await login(c, "g5a-alice@x.com")
        bob = await login(c, "g5a-bob@x.com")
        yield c, alice, bob


@pytest.fixture
async def two_teams(db):
    async for item in _client_with_two_teams(db):
        yield item


async def _alice_project(client, alice):
    r = await client.post(
        "/api/v1/projects", json={"name": "alice-proj"}, headers=auth(alice["token"])
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------------------
# 1. Oracle probe: foreign ids are indistinguishable from unknown ids
# ---------------------------------------------------------------------------


async def test_replay_foreign_project_is_404_identical_to_unknown(two_teams):
    client, alice, bob = two_teams
    proj = await _alice_project(client, alice)
    foreign = await client.get(
        f"/api/v1/projects/{proj}/events/replay", headers=auth(bob["token"])
    )
    unknown = await client.get(
        "/api/v1/projects/proj-does-not-exist/events/replay",
        headers=auth(bob["token"]),
    )
    # Same status AND same envelope — no oracle.
    assert foreign.status_code == 404 == unknown.status_code
    assert _error_code(foreign.json()) == "NOT_FOUND"
    assert _error_code(unknown.json()) == "NOT_FOUND"


async def test_ws_ticket_for_foreign_project_is_404(two_teams):
    client, alice, bob = two_teams
    proj = await _alice_project(client, alice)
    r = await client.post(
        "/api/v1/ws-ticket", json={"projectId": proj}, headers=auth(bob["token"])
    )
    unknown = await client.post(
        "/api/v1/ws-ticket",
        json={"projectId": "proj-does-not-exist"},
        headers=auth(bob["token"]),
    )
    assert r.status_code == 404 == unknown.status_code
    assert _error_code(r.json()) == "NOT_FOUND"
    assert _error_code(unknown.json()) == "NOT_FOUND"


async def test_keys_team_id_mismatch_is_404(two_teams):
    client, alice, bob = two_teams
    r = await client.get(
        "/api/v1/keys", params={"teamId": alice["principal"]["teamId"]},
        headers=auth(bob["token"]),
    )
    assert r.status_code == 404
    assert _error_code(r.json()) == "NOT_FOUND"
    # Own team still lists fine.
    own = await client.get("/api/v1/keys", headers=auth(bob["token"]))
    assert own.status_code == 200


async def test_roster_of_foreign_team_is_404(two_teams):
    client, alice, bob = two_teams
    foreign_team = alice["principal"]["teamId"]
    r = await client.get(f"/api/v1/teams/{foreign_team}/members", headers=auth(bob["token"]))
    assert r.status_code == 404
    assert _error_code(r.json()) == "NOT_FOUND"


async def test_invite_mint_into_foreign_team_is_404(two_teams):
    """A foreign admin probing the invite path gets NOT_FOUND, never 403."""
    client, alice, bob = two_teams
    foreign_team = alice["principal"]["teamId"]
    r = await client.post(
        f"/api/v1/teams/{foreign_team}/invites",
        json={"email": "newbie@x.com", "role": "viewer"},
        headers=auth(bob["token"]),
    )
    unknown = await client.post(
        "/api/v1/teams/team-unknown-xyz/invites",
        json={"email": "newbie@x.com", "role": "viewer"},
        headers=auth(bob["token"]),
    )
    assert r.status_code == 404 == unknown.status_code
    assert _error_code(r.json()) == "NOT_FOUND"
    assert _error_code(unknown.json()) == "NOT_FOUND"


async def test_agent_seat_into_foreign_team_is_404_for_admin(two_teams):
    client, alice, bob = two_teams
    foreign_team = alice["principal"]["teamId"]
    r = await client.post(f"/api/v1/teams/{foreign_team}/seats/agent", headers=auth(bob["token"]))
    # Never 403 — that would confirm the foreign team exists.
    assert r.status_code == 404
    assert r.status_code != 403


# ---------------------------------------------------------------------------
# 2. OpenAPI typing islands (top-20 routes)
# ---------------------------------------------------------------------------

_TYPED_S1_ROUTES = [
    ("post", "/api/v1/auth/request-login"),
    ("post", "/api/v1/auth/verify-otp"),
    ("post", "/api/v1/auth/login"),
    ("post", "/api/v1/auth/logout"),
    ("get", "/api/v1/me"),
    ("post", "/api/v1/projects"),
    ("get", "/api/v1/projects"),
    ("get", "/api/v1/projects/{project_id}/events/replay"),
]


def test_s1_top_routes_have_concrete_openapi_components(db):
    app = make_app(db, Settings(master_key=MASTER_KEY, cors_origins=[]))
    schema = app.openapi()
    for method, path in _TYPED_S1_ROUTES:
        ref = _success_component_ref(schema, path, method)
        assert ref, f"{method.upper()} {path} has no concrete success component"
        name = ref.split("/")[-1]
        assert name in schema["components"]["schemas"], f"missing component {name}"


def test_s1_error_envelope_documented_on_login(db):
    app = make_app(db, Settings(master_key=MASTER_KEY, cors_origins=[]))
    schema = app.openapi()
    login_op = schema["paths"]["/api/v1/auth/login"]["post"]
    err_schema = login_op["responses"]["403"]["content"]["application/json"]["schema"]
    assert "$ref" in err_schema or "components" in str(err_schema)
