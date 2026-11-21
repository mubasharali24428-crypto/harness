"""P-17 client trust portal: per-project guest grants and the guest gate.

The gate is exercised through BOTH principal resolvers (Section 1's own and the
shared `make_bearer_dep` every other service uses), on stand-in routes mounted at
the real paths. The stand-ins return a constant: what is under test is whether
the request is let through at all.
"""

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from harness_contracts import hash_token, make_bearer_dep
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.auth import get_principal
from harness_identity.models import (
    AgentRun,
    AuditLog,
    AuthSession,
    Base,
    Project,
    ProjectGuestGrant,
    PullRequest,
    Team,
    TeamMember,
    User,
)
from harness_identity.models import Role as DbRole
from harness_identity.portal_api import router

RESOLVERS = ["section1", "shared"]


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _app(factory, resolver: str) -> FastAPI:
    app = FastAPI()
    app.state.session_factory = factory
    app.include_router(router)
    dep = get_principal if resolver == "section1" else make_bearer_dep(factory)

    async def ok(_p=Depends(dep)):  # noqa: B008
        return {"ok": True}

    for path in (
        "/api/v1/orchestrator/runs",
        "/api/v1/orchestrator/runs/{run_id}",
        "/api/v1/orchestrator/runs/{run_id}/plan",
        "/api/v1/orchestrator/runs/{run_id}/collab/history",  # not listed for guests
        "/api/v1/reviews/{pr_id}",
        "/api/v1/reviews/{pr_id}/comments",  # not listed for guests
        "/api/v1/me",
    ):
        app.add_api_route(path, ok, methods=["GET"])
    app.add_api_route("/api/v1/orchestrator/runs/{run_id}", ok, methods=["POST", "DELETE"])
    app.add_api_route("/api/v1/reviews/{pr_id}/approve", ok, methods=["POST"])
    return app


async def _person(db, team_id: str, role: DbRole) -> tuple[str, dict]:
    user = User(email=f"{secrets.token_hex(4)}@example.test")
    db.add(user)
    await db.flush()
    db.add(TeamMember(team_id=team_id, user_id=user.id, role=role, display_name=role.value))
    token = secrets.token_urlsafe(16)
    db.add(
        AuthSession(
            token_hash=hash_token(token), user_id=user.id, team_id=team_id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await db.flush()
    return user.id, {"authorization": f"Bearer {token}"}


async def _project(db, team_id: str, name: str) -> dict:
    """A project with one run and one pull request; returns their ids."""
    project = Project(team_id=team_id, name=name)
    db.add(project)
    await db.flush()
    run = AgentRun(project_id=project.id)
    db.add(run)
    await db.flush()
    pr = PullRequest(project_id=project.id, run_id=run.id, github_pr_number=1, pr_url="http://pr")
    db.add(pr)
    await db.flush()
    return {"id": project.id, "run": run.id, "pr": pr.id}


@pytest.fixture
async def world(factory):
    """Team A with projects `mine` (granted) and `other` (not); team B with `foreign`."""
    async with factory() as db:
        a, b = Team(name="A"), Team(name="B")
        db.add_all([a, b])
        await db.flush()
        w = {
            "team": a.id,
            "mine": await _project(db, a.id, "mine"),
            "other": await _project(db, a.id, "other"),
            "foreign": await _project(db, b.id, "foreign"),
        }
        w["admin_id"], w["admin"] = await _person(db, a.id, DbRole.ADMIN)
        w["engineer_id"], w["engineer"] = await _person(db, a.id, DbRole.ENGINEER)
        w["guest_id"], w["guest"] = await _person(db, a.id, DbRole.GUEST)
        _, w["admin_b"] = await _person(db, b.id, DbRole.ADMIN)
        await db.commit()
    return w


def _client(factory, resolver="section1") -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_app(factory, resolver)), base_url="http://t")


async def _grant(factory, w, project="mine"):
    async with _client(factory) as c:
        r = await c.put(f"/api/v1/projects/{w[project]['id']}/guests/{w['guest_id']}",
                        headers=w["admin"])
        assert r.status_code == 200, r.text


# ------------------------------------------------------------------ the gate


@pytest.mark.parametrize("resolver", RESOLVERS)
async def test_guest_without_a_grant_reads_nothing(factory, world, resolver):
    async with _client(factory, resolver) as c:
        for path in (
            f"/api/v1/orchestrator/runs/{world['mine']['run']}",
            f"/api/v1/orchestrator/runs?projectId={world['mine']['id']}",
            f"/api/v1/reviews/{world['mine']['pr']}",
        ):
            r = await c.get(path, headers=world["guest"])
            assert r.status_code == 403, path
            assert r.json()["detail"]["error"]["code"] == "GUEST_PORTAL_ONLY"


@pytest.mark.parametrize("resolver", RESOLVERS)
async def test_granted_guest_reads_only_that_project(factory, world, resolver):
    await _grant(factory, world)
    mine, other, foreign = world["mine"], world["other"], world["foreign"]
    async with _client(factory, resolver) as c:
        g = world["guest"]
        for path in (
            f"/api/v1/orchestrator/runs/{mine['run']}",
            f"/api/v1/orchestrator/runs/{mine['run']}/plan",
            f"/api/v1/orchestrator/runs?projectId={mine['id']}",
            f"/api/v1/reviews/{mine['pr']}",
            "/api/v1/me",
        ):
            assert (await c.get(path, headers=g)).status_code == 200, path
        for path in (
            # the same team's other client, and another team entirely
            f"/api/v1/orchestrator/runs/{other['run']}",
            f"/api/v1/orchestrator/runs/{other['run']}/plan",
            f"/api/v1/orchestrator/runs?projectId={other['id']}",
            f"/api/v1/reviews/{other['pr']}",
            f"/api/v1/orchestrator/runs/{foreign['run']}",
            f"/api/v1/reviews/{foreign['pr']}",
            # the unfiltered team list, an unknown run, and routes that are not listed
            "/api/v1/orchestrator/runs",
            "/api/v1/orchestrator/runs/no-such-run",
            f"/api/v1/orchestrator/runs/{mine['run']}/collab/history",
            f"/api/v1/reviews/{mine['pr']}/comments",
        ):
            assert (await c.get(path, headers=g)).status_code == 403, path
        # Nothing but GET, even inside the granted project.
        run_url = f"/api/v1/orchestrator/runs/{mine['run']}"
        assert (await c.post(run_url, headers=g)).status_code == 403
        assert (await c.delete(run_url, headers=g)).status_code == 403
        assert (await c.post(f"/api/v1/reviews/{mine['pr']}/approve", headers=g)).status_code == 403


@pytest.mark.parametrize("resolver", RESOLVERS)
async def test_revoking_the_grant_closes_the_project_at_once(factory, world, resolver):
    await _grant(factory, world)
    path = f"/api/v1/orchestrator/runs/{world['mine']['run']}"
    async with _client(factory, resolver) as c:
        assert (await c.get(path, headers=world["guest"])).status_code == 200
    async with _client(factory) as c:
        r = await c.delete(f"/api/v1/projects/{world['mine']['id']}/guests/{world['guest_id']}",
                           headers=world["admin"])
        assert r.status_code == 204
    async with _client(factory, resolver) as c:
        assert (await c.get(path, headers=world["guest"])).status_code == 403


@pytest.mark.parametrize("resolver", RESOLVERS)
async def test_a_grant_in_another_team_does_not_carry_over(factory, world, resolver):
    """A grant row that points at another team's project is inert for this session."""
    async with factory() as db:
        db.add(ProjectGuestGrant(project_id=world["foreign"]["id"], user_id=world["guest_id"],
                                 granted_by="x"))
        await db.commit()
    async with _client(factory, resolver) as c:
        r = await c.get(f"/api/v1/orchestrator/runs/{world['foreign']['run']}",
                        headers=world["guest"])
        assert r.status_code == 403


@pytest.mark.parametrize("resolver", RESOLVERS)
async def test_members_are_not_gated(factory, world, resolver):
    async with _client(factory, resolver) as c:
        r = await c.get(f"/api/v1/orchestrator/runs/{world['other']['run']}/collab/history",
                        headers=world["engineer"])
        assert r.status_code == 200


@pytest.mark.parametrize("resolver", RESOLVERS)
async def test_gate_path_shape_is_exact(factory, world, resolver):
    """Listed-shape look-alikes are not listed (packet §6.1).

    Anchors are \\Z, not $: re's $ also matches before a trailing newline, so a
    stack that delivers a raw trailing newline inside scope["path"] (the ASGI
    test transport normalizes it away; uvicorn need not) would pass a
    $-anchored gate and then die at the router — permitted-but-useless, which a
    deny-by-default gate must not be. Double slashes, case shifts, trailing
    slashes and path-tricks in an id never match; the gate answers 403 before
    the router sees them.
    """
    await _grant(factory, world)
    mine = world["mine"]
    async with _client(factory, resolver) as c:
        g = world["guest"]
        # The transport normalizes %0A away, so this collapses to the canonical
        # listed route (200). Under a stack that keeps the newline in the path,
        # the \Z anchors turn the same request into a 403 instead of a pass.
        normalized = await c.get("/api/v1/me%0A", headers=g)
        assert normalized.status_code == 200, normalized.text
        # Trailing slashes redirect at the ROUTER, which runs before the gate's
        # resolver — a 307 with no data. Following it lands on the canonical
        # path, where the gate applies for real.
        for path in ("/api/v1/me/", "/api/v1/portal/projects/",
                     f"/api/v1/orchestrator/runs/{mine['run']}/plan/"):
            r = await c.get(path, headers=g)
            assert r.status_code == 307, path
            assert r.headers["location"].rstrip("/").endswith(path.rstrip("/"))
            followed = await c.get(path, headers=g, follow_redirects=True)
            assert followed.status_code == 200, path
        # No-route look-alikes answer 404 (no data, gate never reached), shape-
        # broken ids that route are 403 at the gate, and anything the ASGI layer
        # normalizes (%2F decodes, like a trailing slash) redirects to the
        # canonical path — where the gate applies for real. None of them return
        # data before the gate.
        for path in (
            "/api/v1//me",
            "/api/v1/ME",
            "/api/v1/orchestrator/runs/%2e%2e%2f" + mine["run"],  # traversal-looking id
            f"/api/v1/reviews/{mine['pr']}%2f",
        ):
            r = await c.get(path, headers=g)
            assert r.status_code in (307, 403, 404), path
            if r.status_code == 307:
                followed = await c.get(path, headers=g, follow_redirects=True)
                assert followed.status_code in (200, 403, 404), path


@pytest.mark.parametrize("resolver", RESOLVERS)
async def test_repeated_project_id_query_is_read_like_the_handler_does(factory, world, resolver):
    """Packet §6.2: ``?projectId=A&projectId=B`` — no first/last divergence.

    The gate and the runs-list handler both read through Starlette's
    ``QueryParams.get``, which returns the LAST value, so there is no window
    where the gate grants one project and the handler serves another. Pin both
    directions: a request whose last value is granted passes, one whose last
    value is not granted is refused.
    """
    await _grant(factory, world)
    mine, other = world["mine"], world["other"]
    async with _client(factory, resolver) as c:
        g = world["guest"]
        allowed = f"/api/v1/orchestrator/runs?projectId={other['id']}&projectId={mine['id']}"
        assert (await c.get(allowed, headers=g)).status_code == 200, allowed
        refused = f"/api/v1/orchestrator/runs?projectId={mine['id']}&projectId={other['id']}"
        assert (await c.get(refused, headers=g)).status_code == 403, refused


# ------------------------------------------------------------- grant routes


async def test_only_an_admin_of_the_projects_team_can_grant(factory, world):
    url = f"/api/v1/projects/{world['mine']['id']}/guests/{world['guest_id']}"
    async with _client(factory) as c:
        assert (await c.put(url, headers=world["engineer"])).status_code == 403
        assert (await c.put(url, headers=world["guest"])).status_code == 403
        assert (await c.put(url, headers=world["admin_b"])).status_code == 404
        assert (await c.get(f"/api/v1/projects/{world['mine']['id']}/guests",
                            headers=world["admin_b"])).status_code == 404
        assert (await c.delete(url, headers=world["admin_b"])).status_code == 404
    async with factory() as db:
        assert (await db.execute(select(ProjectGuestGrant))).first() is None


async def test_only_a_guest_of_the_team_can_be_granted(factory, world):
    base = f"/api/v1/projects/{world['mine']['id']}/guests"
    async with _client(factory) as c:
        assert (await c.put(
            f"{base}/{world['engineer_id']}", headers=world["admin"])).status_code == 422
        assert (await c.put(f"{base}/nobody", headers=world["admin"])).status_code == 422


async def test_grant_and_revoke_are_audited_once(factory, world):
    await _grant(factory, world)
    await _grant(factory, world)  # repeat: no second row, no second audit entry
    url = f"/api/v1/projects/{world['mine']['id']}/guests"
    async with _client(factory) as c:
        listed = (await c.get(url, headers=world["admin"])).json()["items"]
        assert listed == [{"userId": world["guest_id"], "grantedBy": world["admin_id"]}]
        revoke = f"{url}/{world['guest_id']}"
        assert (await c.delete(revoke, headers=world["admin"])).status_code == 204
        assert (await c.delete(revoke, headers=world["admin"])).status_code == 204
    async with factory() as db:
        actions = [a.action for a in (await db.execute(select(AuditLog))).scalars()]
    assert actions.count("project.guest_granted") == 1
    assert actions.count("project.guest_revoked") == 1


async def test_portal_project_list_shows_a_guest_only_their_grants(factory, world):
    async with _client(factory) as c:
        empty = (await c.get("/api/v1/portal/projects", headers=world["guest"])).json()
        assert empty == {"items": []}
        await _grant(factory, world)
        got = (await c.get("/api/v1/portal/projects", headers=world["guest"])).json()["items"]
        assert got == [{"id": world["mine"]["id"], "name": "mine"}]
        team = (await c.get("/api/v1/portal/projects", headers=world["engineer"])).json()["items"]
        assert sorted(p["name"] for p in team) == ["mine", "other"]
