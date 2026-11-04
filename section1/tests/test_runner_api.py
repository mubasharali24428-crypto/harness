"""Marathon 2.0 W13 — bring-your-own runner control plane (Section 1 side).

Self-contained like the skills/memory suites: a scratch FastAPI app mounts the
runner router over an aiosqlite store, sessions are seeded with contract-shaped
token hashes, and the durable run_claims table (raw SQL by design) is created
by the fixture.

Coverage: registration returns a one-time token and never leaks it again;
role matrix (admin mints/revokes/assigns, engineer assigns, viewers read,
guests read, unauth 401); revocation kills the token on the next request;
nonce reuse is refused (replay) and stale timestamps are refused; signed
confinement attestations verify, tampered signatures/mismatched confinement/
unknown keys are refused; a stolen token can neither list, claim nor finish
another team's job (404, same body as unknown); the claim lifecycle holds the
run_claims lease, a second claim is refused, and finish deletes only the
runner's own claim; an expired lease is released and the run is ORPHANED, never
re-queued; every lifecycle action leaves an audit row.
"""

import secrets
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.eventbus import EventBus
from harness_identity.models import (
    AuditLog,
    AuthSession,
    Base,
    Project,
    Team,
    TeamMember,
    User,
)
from harness_identity.runner_api import (
    LEASE_SECONDS,
    derive_attestation_key,
    router,
    sign_attestation,
)


@pytest.fixture
async def runner_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 0006 is raw SQL and deliberately not an ORM model.
        await conn.execute(
            text(
                "CREATE TABLE run_claims (run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
                "state TEXT NOT NULL, claimed_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, "
                "worker_id TEXT NOT NULL)"
            )
        )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def runner_client(runner_factory):
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = runner_factory
    app.state.event_bus = EventBus(runner_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def _seed(factory) -> dict:
    async with factory() as db:
        team_a = Team(name="runners-a")
        team_b = Team(name="runners-b")
        db.add_all([team_a, team_b])
        await db.flush()
        memberships = [
            ("a-admin@x.com", "admin", team_a),
            ("a-eng@x.com", "engineer", team_a),
            ("a-view@x.com", "viewer", team_a),
            ("a-guest@x.com", "guest", team_a),
            ("b-admin@x.com", "admin", team_b),
        ]
        users: dict[str, User] = {}
        tokens: dict[str, str] = {}
        for email, _role, _team in memberships:
            user = User(email=email, name=email.split("@")[0])
            db.add(user)
            users[email] = user
        await db.flush()
        for email, role, team in memberships:
            db.add(
                TeamMember(
                    team_id=team.id,
                    user_id=users[email].id,
                    display_name=users[email].name,
                    role=role,
                )
            )
            token = secrets.token_urlsafe(32)
            db.add(
                AuthSession(
                    token_hash=hash_token(token),
                    user_id=users[email].id,
                    team_id=team.id,
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
            tokens[email] = token
        project_a = Project(team_id=team_a.id, name="proj-a")
        project_b = Project(team_id=team_b.id, name="proj-b")
        db.add_all([project_a, project_b])
        await db.commit()
        await db.execute(
            text(
                "INSERT INTO run_claims (run_id, project_id, state, claimed_at, heartbeat_at, "
                "worker_id) VALUES (:r, :p, 'queued', :n, :n, 'enqueuer')"
            ),
            {"r": "run-a" + "0" * 31, "p": project_a.id, "n": _now_iso()},
        )
        await db.execute(
            text(
                "INSERT INTO run_claims (run_id, project_id, state, claimed_at, heartbeat_at, "
                "worker_id) VALUES (:r, :p, 'queued', :n, :n, 'enqueuer')"
            ),
            {"r": "run-b" + "0" * 31, "p": project_b.id, "n": _now_iso()},
        )
        await db.commit()

    def _actor(email: str) -> dict:
        return {"userId": users[email].id, "token": tokens[email]}

    return {
        "teamA": team_a.id,
        "teamB": team_b.id,
        "admin": _actor("a-admin@x.com"),
        "engineer": _actor("a-eng@x.com"),
        "viewer": _actor("a-view@x.com"),
        "guest": _actor("a-guest@x.com"),
        "foreignAdmin": _actor("b-admin@x.com"),
        "runA": "run-a" + "0" * 31,
        "runB": "run-b" + "0" * 31,
    }


async def _register(client, token: str, name: str = "laptop", confinement: str = "docker") -> dict:
    response = await client.post(
        "/api/v1/team-runners",
        json={"name": name, "description": "fedora workstation", "confinement": confinement},
        headers=_bearer(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _attestation(confinement: str = "docker") -> dict:
    return {
        "schema": 1,
        "confinement": confinement,
        "os": "linux",
        "hostIdSha256": "a" * 64,
        "runnerVersion": "0.1.0",
        "policyDigest": "b" * 64,
    }


def _runner_headers(token: str, nonce: str | None = None, ts: int | None = None) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "X-Runner-Nonce": nonce or secrets.token_hex(12),
        "X-Runner-Timestamp": str(int(time.time()) if ts is None else ts),
    }


async def _assign(client, admin_token: str, runner_id: str, run_id: str):
    return await client.post(
        f"/api/v1/team-runners/{runner_id}/jobs",
        json={"runId": run_id},
        headers=_bearer(admin_token),
    )


async def _audit_actions(factory, action: str) -> list[AuditLog]:
    async with factory() as db:
        return list(
            (await db.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all()
        )


# ------------------------------------------------------------------- tests


async def test_registration_shows_token_once_and_hides_it_later(runner_client, runner_factory):
    env = await _seed(runner_factory)
    created = await _register(runner_client, env["admin"]["token"])
    token = created["token"]
    assert token.startswith("hrn_")
    assert created["runner"]["tokenHint"] == token[-4:]
    assert "token" not in created["runner"]

    listed = (
        await runner_client.get(
            "/api/v1/team-runners", headers=_bearer(env["admin"]["token"])
        )
    ).json()
    assert len(listed["runners"]) == 1
    assert "token" not in listed["runners"][0]

    async with runner_factory() as db:
        from harness_identity.models import TeamRunner

        row = (await db.execute(select(TeamRunner))).scalar_one()
        # The clear token is never stored; the verifier key is a derivative.
        assert row.token_sha256 != token
        assert row.token_sha256 == hash_token(token)
        assert row.attestation_key == derive_attestation_key(token)

    assert len(await _audit_actions(runner_factory, "runner.registered")) == 1


async def test_role_matrix(runner_client, runner_factory):
    env = await _seed(runner_factory)
    assert (
        await runner_client.post(
            "/api/v1/team-runners",
            json={"name": "laptop", "description": "x", "confinement": "docker"},
            headers=_bearer(env["engineer"]["token"]),
        )
    ).status_code == 403
    assert (await runner_client.get("/api/v1/team-runners")).status_code == 401
    assert (
        await runner_client.get("/api/v1/team-runners", headers=_bearer(env["viewer"]["token"]))
    ).status_code == 200
    assert (
        await runner_client.get("/api/v1/team-runners", headers=_bearer(env["guest"]["token"]))
    ).status_code == 200
    created = await _register(runner_client, env["admin"]["token"])
    assert (
        await runner_client.post(
            f"/api/v1/team-runners/{created['runner']['id']}/revoke",
            headers=_bearer(env["engineer"]["token"]),
        )
    ).status_code == 403


async def test_duplicate_name_is_refused(runner_client, runner_factory):
    env = await _seed(runner_factory)
    await _register(runner_client, env["admin"]["token"])
    duplicate = await runner_client.post(
        "/api/v1/team-runners",
        json={"name": "laptop", "description": "second", "confinement": "docker"},
        headers=_bearer(env["admin"]["token"]),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["error"]["code"] == "NAME_TAKEN"


async def test_revocation_kills_the_token_immediately(runner_client, runner_factory):
    env = await _seed(runner_factory)
    created = await _register(runner_client, env["admin"]["token"])
    token = created["token"]
    runner_id = created["runner"]["id"]

    ok = await runner_client.post(
        "/api/v1/runner/heartbeat",
        json={"attestation": _attestation(), "signature": sign_attestation(_attestation(), token)},
        headers=_runner_headers(token),
    )
    assert ok.status_code == 200, ok.text

    revoked = await runner_client.post(
        f"/api/v1/team-runners/{runner_id}/revoke", headers=_bearer(env["admin"]["token"])
    )
    assert revoked.status_code == 204
    again = await runner_client.post(
        f"/api/v1/team-runners/{runner_id}/revoke", headers=_bearer(env["admin"]["token"])
    )
    assert again.status_code == 204  # idempotent

    dead = await runner_client.post(
        "/api/v1/runner/heartbeat",
        json={"attestation": _attestation(), "signature": sign_attestation(_attestation(), token)},
        headers=_runner_headers(token),
    )
    assert dead.status_code == 401

    async with runner_factory() as db:
        nonces = (await db.execute(text("SELECT COUNT(*) FROM runner_nonces"))).scalar_one()
        # The revoked request wrote no nonce: token status precedes the burn.
        assert nonces == 1
    assert len(await _audit_actions(runner_factory, "runner.revoked")) == 1


async def test_replay_and_stale_timestamp_are_refused(runner_client, runner_factory):
    env = await _seed(runner_factory)
    created = await _register(runner_client, env["admin"]["token"])
    token = created["token"]
    body = {"attestation": _attestation(), "signature": sign_attestation(_attestation(), token)}

    nonce = secrets.token_hex(12)
    first = await runner_client.post(
        "/api/v1/runner/heartbeat", json=body, headers=_runner_headers(token, nonce=nonce)
    )
    assert first.status_code == 200, first.text
    replayed = await runner_client.post(
        "/api/v1/runner/heartbeat", json=body, headers=_runner_headers(token, nonce=nonce)
    )
    assert replayed.status_code == 409
    assert replayed.json()["detail"]["error"]["code"] == "REPLAY_REJECTED"

    stale = await runner_client.post(
        "/api/v1/runner/heartbeat",
        json=body,
        headers=_runner_headers(token, ts=int(time.time()) - 3600),
    )
    assert stale.status_code == 401
    assert stale.json()["detail"]["error"]["code"] == "STALE_REQUEST"


async def test_attestation_verification(runner_client, runner_factory):
    env = await _seed(runner_factory)
    created = await _register(runner_client, env["admin"]["token"])
    token = created["token"]
    good = _attestation()

    tampered = await runner_client.post(
        "/api/v1/runner/heartbeat",
        json={"attestation": good, "signature": "0" * 64},
        headers=_runner_headers(token),
    )
    assert tampered.status_code == 409
    assert tampered.json()["detail"]["error"]["code"] == "ATTESTATION_MISMATCH"

    mismatched = _attestation("process")
    mismatch = await runner_client.post(
        "/api/v1/runner/heartbeat",
        json={"attestation": mismatched, "signature": sign_attestation(mismatched, token)},
        headers=_runner_headers(token),
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["error"]["code"] == "ATTESTATION_MISMATCH"

    smuggled = {**good, "command": "rm -rf /"}
    unknown = await runner_client.post(
        "/api/v1/runner/heartbeat",
        json={"attestation": smuggled, "signature": sign_attestation(smuggled, token)},
        headers=_runner_headers(token),
    )
    assert unknown.status_code == 422

    verified = await runner_client.post(
        "/api/v1/runner/heartbeat",
        json={"attestation": good, "signature": sign_attestation(good, token)},
        headers=_runner_headers(token),
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["verified"] is True
    assert len(await _audit_actions(runner_factory, "runner.attested")) == 1


async def test_stolen_token_cannot_reach_another_team(runner_client, runner_factory):
    env = await _seed(runner_factory)
    foreign = await _register(
        runner_client, env["foreignAdmin"]["token"], name="b-laptop"
    )
    stolen = foreign["token"]

    # Team B's token sees only team B jobs, and can never claim team A's run.
    listed = await runner_client.get("/api/v1/runner/jobs", headers=_runner_headers(stolen))
    assert listed.status_code == 200
    assert listed.json()["jobs"] == []
    claim_foreign = await runner_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/claim", headers=_runner_headers(stolen)
    )
    assert claim_foreign.status_code == 404
    # Unknown and foreign ids produce byte-identical refusals.
    claim_unknown = await runner_client.post(
        f"/api/v1/runner/jobs/{'run-z' + '0' * 31}/claim", headers=_runner_headers(stolen)
    )
    assert claim_unknown.status_code == 404
    assert claim_foreign.json() == claim_unknown.json()
    finish_foreign = await runner_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/finish",
        json={"status": "completed"},
        headers=_runner_headers(stolen),
    )
    assert finish_foreign.status_code == 404

    # Team B's admin cannot assign team A's run to a team B runner.
    response = await _assign(
        runner_client, env["foreignAdmin"]["token"], foreign["runner"]["id"], env["runA"]
    )
    assert response.status_code == 404
    unknown = await _assign(
        runner_client,
        env["foreignAdmin"]["token"],
        foreign["runner"]["id"],
        "run-z" + "0" * 31,
    )
    assert unknown.status_code == 404
    assert response.json() == unknown.json()

    # A team B admin cannot revoke a team A runner either.
    admin_runner = await _register(runner_client, env["admin"]["token"])
    cross = await runner_client.post(
        f"/api/v1/team-runners/{admin_runner['runner']['id']}/revoke",
        headers=_bearer(env["foreignAdmin"]["token"]),
    )
    assert cross.status_code == 404


async def test_job_lifecycle_holds_the_lease_and_finishes(runner_client, runner_factory):
    env = await _seed(runner_factory)
    created = await _register(runner_client, env["admin"]["token"])
    runner_id, token = created["runner"]["id"], created["token"]

    assigned = await _assign(runner_client, env["engineer"]["token"], runner_id, env["runA"])
    assert assigned.status_code == 201, assigned.text
    assert assigned.json()["job"]["state"] == "offered"

    # A second offer for the same run (even to another runner) is refused.
    other = await _register(runner_client, env["admin"]["token"], name="desktop")
    taken = await _assign(runner_client, env["admin"]["token"], other["runner"]["id"], env["runA"])
    assert taken.status_code == 409

    listed = await runner_client.get("/api/v1/runner/jobs", headers=_runner_headers(token))
    assert [job["runId"] for job in listed.json()["jobs"]] == [env["runA"]]

    claim = await runner_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/claim", headers=_runner_headers(token)
    )
    assert claim.status_code == 200, claim.text
    assert claim.json()["job"]["state"] == "leased"
    assert claim.json()["leaseSeconds"] == LEASE_SECONDS

    async with runner_factory() as db:
        row = (
            await db.execute(
                text("SELECT state, worker_id FROM run_claims WHERE run_id = :r"),
                {"r": env["runA"]},
            )
        ).mappings().one()
        assert row["state"] == "running"
        assert row["worker_id"] == f"runner:{runner_id}"

    second = await runner_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/claim", headers=_runner_headers(token)
    )
    assert second.status_code == 409
    assert second.json()["detail"]["error"]["code"] == "JOB_NOT_OFFERED"

    beat = await runner_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/heartbeat", headers=_runner_headers(token)
    )
    assert beat.status_code == 200, beat.text
    assert beat.json()["state"] == "running"

    finished = await runner_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/finish",
        json={"status": "completed", "resultDigest": "c" * 64},
        headers=_runner_headers(token),
    )
    assert finished.status_code == 200, finished.text
    assert finished.json()["state"] == "completed"

    async with runner_factory() as db:
        remaining = (
            await db.execute(
                text("SELECT COUNT(*) FROM run_claims WHERE run_id = :r"), {"r": env["runA"]}
            )
        ).scalar_one()
        assert remaining == 0

    again = await runner_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/finish",
        json={"status": "completed"},
        headers=_runner_headers(token),
    )
    assert again.status_code == 409

    assert len(await _audit_actions(runner_factory, "runner.job_assigned")) == 1
    assert len(await _audit_actions(runner_factory, "runner.job_completed")) == 1


async def test_assign_refuses_a_non_queued_run(runner_client, runner_factory):
    env = await _seed(runner_factory)
    created = await _register(runner_client, env["admin"]["token"])
    async with runner_factory() as db:
        await db.execute(
            text("UPDATE run_claims SET state = 'running' WHERE run_id = :r"), {"r": env["runA"]}
        )
        await db.commit()
    response = await _assign(
        runner_client, env["admin"]["token"], created["runner"]["id"], env["runA"]
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "NOT_QUEUED"


async def test_expired_lease_is_released_and_the_run_is_orphaned(runner_client, runner_factory):
    env = await _seed(runner_factory)
    created = await _register(runner_client, env["admin"]["token"])
    runner_id, token = created["runner"]["id"], created["token"]
    await _assign(runner_client, env["admin"]["token"], runner_id, env["runA"])
    claim = await runner_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/claim", headers=_runner_headers(token)
    )
    assert claim.status_code == 200, claim.text

    async with runner_factory() as db:
        from harness_identity.models import RunnerJob

        job = (
            await db.execute(select(RunnerJob).where(RunnerJob.run_id == env["runA"]))
        ).scalar_one()
        job.lease_expires_at = datetime.now(UTC) - timedelta(hours=1)
        await db.commit()

    beat = await runner_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/heartbeat", headers=_runner_headers(token)
    )
    assert beat.status_code == 409
    assert beat.json()["detail"]["error"]["code"] == "JOB_NOT_LEASED"

    async with runner_factory() as db:
        state = (
            await db.execute(
                text("SELECT state FROM run_claims WHERE run_id = :r"), {"r": env["runA"]}
            )
        ).scalar_one()
        # Never re-queued: the remote host may still be executing it.
        assert state == "orphaned"
        job_state = (
            await db.execute(
                text("SELECT state FROM runner_jobs WHERE run_id = :r"), {"r": env["runA"]}
            )
        ).scalar_one()
        assert job_state == "released"


async def test_anonymous_malformed_body_is_401_not_422(runner_client, runner_factory):
    """Auth is a dependency, so it runs before the body is validated."""
    env = await _seed(runner_factory)
    anonymous = await runner_client.post("/api/v1/runner/heartbeat", json={})
    assert anonymous.status_code == 401
    assert anonymous.json()["detail"]["error"]["code"] == "UNAUTHORIZED"

    created = await _register(runner_client, env["admin"]["token"], name="rot")
    bad = await runner_client.post(
        "/api/v1/runner/heartbeat",
        json={"attestation": {}, "signature": "x"},
        headers=_runner_headers(created["token"]),
    )
    assert bad.status_code == 422  # authenticated callers do get validation detail


async def test_rotation_mints_a_new_token_and_kills_the_old_one(runner_client, runner_factory):
    env = await _seed(runner_factory)
    created = await _register(runner_client, env["admin"]["token"])
    runner_id, old = created["runner"]["id"], created["token"]
    body = {"attestation": _attestation(), "signature": sign_attestation(_attestation(), old)}
    assert (
        await runner_client.post(
            "/api/v1/runner/heartbeat", json=body, headers=_runner_headers(old)
        )
    ).status_code == 200

    rotated = await runner_client.post(
        f"/api/v1/team-runners/{runner_id}/rotate", headers=_bearer(env["admin"]["token"])
    )
    assert rotated.status_code == 200, rotated.text
    new = rotated.json()["token"]
    assert new.startswith("hrn_") and new != old
    assert rotated.json()["runner"]["tokenHint"] == new[-4:]

    dead = await runner_client.post(
        "/api/v1/runner/heartbeat", json=body, headers=_runner_headers(old)
    )
    assert dead.status_code == 401

    fresh_att = _attestation()
    alive = await runner_client.post(
        "/api/v1/runner/heartbeat",
        json={"attestation": fresh_att, "signature": sign_attestation(fresh_att, new)},
        headers=_runner_headers(new),
    )
    assert alive.status_code == 200, alive.text

    # The old token's attestation key is dead too: old signatures fail with the
    # new token until the runner re-signs (it just did above with `new`).
    stale_sig = await runner_client.post(
        "/api/v1/runner/heartbeat",
        json={"attestation": fresh_att, "signature": sign_attestation(fresh_att, old)},
        headers=_runner_headers(new),
    )
    assert stale_sig.status_code == 409

    assert len(await _audit_actions(runner_factory, "runner.rotated")) == 1

    revoked = await runner_client.post(
        f"/api/v1/team-runners/{runner_id}/revoke", headers=_bearer(env["admin"]["token"])
    )
    assert revoked.status_code == 204
    refused = await runner_client.post(
        f"/api/v1/team-runners/{runner_id}/rotate", headers=_bearer(env["admin"]["token"])
    )
    assert refused.status_code == 409


async def test_nonce_purge_removes_only_expired_rows(runner_client, runner_factory):
    from harness_identity.runner_api import purge_runner_nonces

    env = await _seed(runner_factory)
    created = await _register(runner_client, env["admin"]["token"])
    runner_id = created["runner"]["id"]
    from harness_identity.models import RunnerNonce

    async with runner_factory() as db:
        db.add_all(
            [
                RunnerNonce(
                    runner_id=runner_id,
                    nonce="old-nonce-value-000",
                    seen_at=datetime.now(UTC) - timedelta(hours=1),
                ),
                RunnerNonce(
                    runner_id=runner_id,
                    nonce="new-nonce-value-000",
                    seen_at=datetime.now(UTC),
                ),
            ]
        )
        await db.commit()

    removed = await purge_runner_nonces(runner_factory)
    assert removed == 1
    async with runner_factory() as db:
        left = (await db.execute(text("SELECT nonce FROM runner_nonces"))).scalars().all()
    assert left == ["new-nonce-value-000"]
