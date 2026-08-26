"""Mission 11 W1-A2 — audit emission wiring tests (C-002).

One assertion block per pinned registry action with correct actor/team/target
ids, plus the two invariants the brief pins:

- append-only guard still holds (ORM update/delete of audit rows blocked);
- emission NEVER throws into the request path (sink failure degrades to a
  warning log and the request succeeds).

Self-contained by design (conftest.py is frozen): fixtures are local and the
routers under test mount on a SCRATCH FastAPI app, same pattern as the
wave-10 suites. Everything runs offline on aiosqlite.

plan.rejected / run.started / run.stopped have NO section-1 call site by
design: reject_plan and start/stop handlers live in section4 files owned by
other lanes (W1-D1 / W1-B-coordination), so A2 ships the helpers as the
merge seam instead — asserted here at helper level.
"""

import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import harness_identity.audit_emit as audit_emit_module
from harness_identity.api_keys import router as keys_router
from harness_identity.audit import APPEND_ONLY_ACTIONS, register_append_only_guard
from harness_identity.audit_emit import (
    _scrub,
    emit_apikey_added,
    emit_apikey_removed,
    emit_billing_plan_changed,
    emit_invite_minted,
    emit_invite_redeemed,
    emit_invite_resent,
    emit_invite_revoked,
    emit_plan_rejected,
    emit_run_started,
    emit_run_stopped,
)
from harness_identity.billing import router as billing_router
from harness_identity.config import Settings
from harness_identity.crypto import KeyVault
from harness_identity.invites import router as invites_router
from harness_identity.models import ApiKey, AuditLog, AuthSession, Base, Team, TeamMember, User

# gitleaks:allow — fake fixture value, never a real credential
RAW_KEY = "«redacted:sk-test-not-a-real-key»"

# Pinned registry (part4 §2 BRIEF W1-A2) -> section-1 call site expectation.
REGISTRY_S1_CALLED = {
    "apikey.added": "emit_apikey_added",
    "apikey.removed": "emit_apikey_removed",
    "invite.minted": "emit_invite_minted",
    "invite.redeemed": "emit_invite_redeemed",
    "invite.revoked": "emit_invite_revoked",
    # M14-F2a: admin resend (old token revoked in the same transaction).
    "invite.resent": "emit_invite_resent",
    "billing.plan_changed": "emit_billing_plan_changed",
}
# Consumed OUTSIDE section1 (W1-D1 state_machine/signals; section4 run routes):
REGISTRY_COORDINATION = ("plan.rejected", "run.started", "run.stopped")


# --------------------------------------------------------------- fixtures


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def client(factory):
    """Scratch app exposing the three S1 routers whose handlers must emit.
    state mirrors what make_app() sets that our handlers read: session_factory
    + vault (key mint encrypts through it)."""
    app = FastAPI()
    app.include_router(keys_router)
    app.include_router(invites_router)
    app.include_router(billing_router)
    app.state.session_factory = factory
    app.state.vault = KeyVault(
        Settings(master_key=b"test-master-key-0123456789abcdef", cors_origins=[]).master_key
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_user(
    factory_, email: str, *, role: str = "admin", team_id: str | None = None
) -> dict:
    """Create team/user/membership/session rows directly; return ids + bearer."""
    import secrets

    async with factory_() as db:
        if team_id is None:
            team = Team(name=f"team-{email}")
            db.add(team)
            await db.flush()
            team_id = team.id
        user = User(email=email.lower(), name=email.split("@")[0])
        db.add(user)
        await db.flush()
        member = TeamMember(
            team_id=team_id, user_id=user.id, display_name=user.name, role=role
        )
        db.add(member)
        await db.flush()
        member_id = member.id
        token = secrets.token_urlsafe(32)
        db.add(
            AuthSession(
                token_hash=hash_token(token),
                user_id=user.id,
                team_id=team_id,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await db.commit()
    return {
        "teamId": team_id,
        "userId": user.id,
        "memberId": member_id,
        "token": token,
    }


async def _audit_rows(factory_) -> list[AuditLog]:
    async with factory_() as db:
        rows = (await db.execute(select(AuditLog).order_by(AuditLog.seq))).scalars().all()
        return list(rows)


# ------------------------------------------------------- registry integrity


def test_registry_actions_are_allowlisted():
    """Every pinned registry action is writable through write_audit."""
    for action in (*REGISTRY_S1_CALLED, *REGISTRY_COORDINATION):
        assert action in APPEND_ONLY_ACTIONS, f"{action} missing from allowlist"


def test_zero_allowlist_gaps_every_s1_action_has_call_site():
    """Grep-probe (VERIFY-CONTRACT): zero 'allowlist without call site' gaps.

    Each section-1 registry action's emit helper must appear in the handler
    module that owns its pinned call site. Coordination actions are exempt —
    their call sites live in section4 files owned by other lanes.
    """
    pkg_dir = pathlib.Path(audit_emit_module.__file__).parent
    homes = {
        "emit_apikey_added": "api_keys.py",
        "emit_apikey_removed": "api_keys.py",
        "emit_invite_minted": "invites.py",
        "emit_invite_redeemed": "invites.py",
        "emit_invite_revoked": "invites.py",
        "emit_invite_resent": "invites.py",  # M14-F2a
        "emit_billing_plan_changed": "billing.py",
    }
    for helper, filename in homes.items():
        text = (pkg_dir / filename).read_text(encoding="utf-8")
        assert f"{helper}(" in text, f"{filename} never calls {helper}()"


# ------------------------------------------------------------ key handlers


async def test_key_mint_and_revoke_emit_rows_with_correct_ids(factory, client):
    admin = await _seed_user(factory, "boss@x.com")

    r = await client.post(
        "/api/v1/keys",
        json={"label": "prod", "provider": "openai", "key": RAW_KEY},
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 201, r.text
    created = r.json()

    rows = await _audit_rows(factory)
    added = [row for row in rows if row.action == "apikey.added"]
    assert len(added) == 1
    row = added[0]
    # Correct actor/team/target ids: actor resolves to the caller's membership.
    assert row.actor_member_id == admin["memberId"]
    assert row.actor_kind == "human"
    assert row.target_type == "api_key"
    assert row.target_id == created["id"]
    assert row.detail["teamId"] == admin["teamId"]
    assert row.detail["provider"] == "openai"
    # No secrets in audit content: raw key nowhere in the row or whole log.
    assert RAW_KEY not in str(row.detail)

    # --- revoke
    r = await client.delete(f"/api/v1/keys/{created['id']}", headers=_bearer(admin["token"]))
    assert r.status_code == 204
    rows = await _audit_rows(factory)
    removed = [row for row in rows if row.action == "apikey.removed"]
    assert len(removed) == 1
    assert removed[0].actor_member_id == admin["memberId"]
    assert removed[0].target_id == created["id"]

    # Idempotent double-revoke stays silent (one event, not two).
    r = await client.delete(f"/api/v1/keys/{created['id']}", headers=_bearer(admin["token"]))
    assert r.status_code == 204
    rows = await _audit_rows(factory)
    assert len([row for row in rows if row.action == "apikey.removed"]) == 1


# --------------------------------------------------------- invite handlers


async def test_invite_lifecycle_emits_and_never_leaks_token(factory, client):
    admin = await _seed_user(factory, "admin@x.com")
    joiner = await _seed_user(factory, "join@y.com")

    # --- mint
    r = await client.post(
        f"/api/v1/teams/{admin['teamId']}/invites",
        json={"email": "new@y.com", "role": "engineer"},
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 201, r.text
    invite = r.json()
    raw_token = invite["token"]

    rows = await _audit_rows(factory)
    minted = [row for row in rows if row.action == "invite.minted"]
    assert len(minted) == 1
    assert minted[0].actor_member_id == admin["memberId"]
    assert minted[0].target_id == invite["id"]
    assert minted[0].detail["email"] == "new@y.com"
    assert minted[0].detail["role"] == "engineer"
    # No secrets: the one-time raw token never enters ANY audit row.
    all_rows = await _audit_rows(factory)
    assert all(raw_token not in str(row.detail) for row in all_rows)

    # --- redeem by a different user (atomic claim + membership insert).
    # M18-C5 (F3): redemption presents the invited email.
    r = await client.post(
        "/api/v1/invites/redeem",
        json={"token": raw_token, "email": "new@y.com"},
        headers=_bearer(joiner["token"]),
    )
    assert r.status_code == 200, r.text
    rows = await _audit_rows(factory)
    redeemed = [row for row in rows if row.action == "invite.redeemed"]
    assert len(redeemed) == 1
    # Actor resolves to the joiner's membership IN THE AFFECTED team (freshly
    # created by this redemption); assert identity semantically.
    async with factory() as db:
        actor = await db.get(TeamMember, redeemed[0].actor_member_id)
    assert actor is not None
    assert actor.user_id == joiner["userId"]
    assert actor.team_id == admin["teamId"]
    assert redeemed[0].target_id == invite["id"]
    assert redeemed[0].detail["teamId"] == admin["teamId"]

    # --- revoke a fresh invite
    r = await client.post(
        f"/api/v1/teams/{admin['teamId']}/invites",
        json={"email": "gone@y.com", "role": "viewer"},
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 201, r.text
    invite2 = r.json()
    r = await client.delete(
        f"/api/v1/teams/{admin['teamId']}/invites/{invite2['id']}",
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 200, r.text
    rows = await _audit_rows(factory)
    revoked = [row for row in rows if row.action == "invite.revoked"]
    assert len(revoked) == 1
    assert revoked[0].actor_member_id == admin["memberId"]
    assert revoked[0].target_id == invite2["id"]

    # --- M14-F2a: resend emits invite.resent and never leaks EITHER token
    r = await client.post(
        f"/api/v1/teams/{admin['teamId']}/invites",
        json={"email": "again@y.com", "role": "engineer"},
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 201, r.text
    invite3 = r.json()
    old_token3, new_token3 = invite3["token"], None
    r = await client.post(
        f"/api/v1/teams/{admin['teamId']}/invites/{invite3['id']}/resend",
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 201, r.text
    new_token3 = r.json()["token"]

    rows = await _audit_rows(factory)
    resent = [row for row in rows if row.action == "invite.resent"]
    assert len(resent) == 1
    assert resent[0].actor_member_id == admin["memberId"]
    assert resent[0].target_type == "team_invite"
    assert resent[0].target_id == invite3["id"]
    assert resent[0].detail["teamId"] == admin["teamId"]
    assert resent[0].detail["role"] == "engineer"
    # No secrets: neither the original nor the fresh token enters ANY audit row.
    all_rows = await _audit_rows(factory)
    for leak in (old_token3, new_token3):
        assert all(leak not in str(row.detail) for row in all_rows)


# --------------------------------------------------------- billing handler


async def test_billing_plan_change_emits_from_to(factory, client):
    admin = await _seed_user(factory, "money@x.com")

    r = await client.put(
        "/api/v1/billing/plan",
        json={"plan": "pro"},
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 200, r.text
    rows = await _audit_rows(factory)
    changed = [row for row in rows if row.action == "billing.plan_changed"]
    assert len(changed) == 1
    assert changed[0].actor_member_id == admin["memberId"]
    assert changed[0].target_type == "billing_plan"
    assert changed[0].detail["to"] == "pro"

    # Second change records the transition honestly (from != to).
    r = await client.put(
        "/api/v1/billing/plan",
        json={"plan": "team"},
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 200, r.text
    rows = await _audit_rows(factory)
    changed = [row for row in rows if row.action == "billing.plan_changed"]
    assert len(changed) == 2
    assert changed[1].detail["from"] == "pro"
    assert changed[1].detail["to"] == "team"
    assert changed[1].target_id == changed[0].target_id  # same upserted row


# ------------------------------------------------- coordination-seam helpers


async def test_coordination_helpers_write_correct_rows(factory):
    """plan.rejected / run.started / run.stopped have no S1 call site (their
    handlers live in section4 lanes) but MUST be usable as the merge seam."""
    admin = await _seed_user(factory, "seam@x.com")
    async with factory() as db:
        await emit_plan_rejected(
            db,
            actor_user_id=admin["userId"],
            team_id=admin["teamId"],
            target_id="run-123",
            detail={"reason": "scope"},
        )
        await emit_run_started(db, run_id="run-123", project_id="proj-1")
        await emit_run_stopped(db, run_id="run-123", project_id="proj-1")
        rows = (
            (await db.execute(select(AuditLog).order_by(AuditLog.seq))).scalars().all()
        )

    by_action = {row.action: row for row in rows}
    assert set(by_action) == {"plan.rejected", "run.started", "run.stopped"}
    rejected = by_action["plan.rejected"]
    assert rejected.actor_member_id == admin["memberId"]
    assert rejected.target_type == "agent_run"
    assert rejected.target_id == "run-123"
    started, stopped = by_action["run.started"], by_action["run.stopped"]
    assert started.target_id == stopped.target_id == "run-123"
    # No acting user passed: agent-kind emission keeps actor NULL rather than
    # fabricating a member id.
    assert started.actor_kind == "agent"
    assert started.actor_member_id is None
    assert started.detail["projectId"] == "proj-1"


async def test_every_s1_helper_writes_exactly_one_row(factory):
    """One row per action, each helper a one-liner over write_audit."""
    admin = await _seed_user(factory, "all@x.com")
    kw = {
        "actor_user_id": admin["userId"],
        "team_id": admin["teamId"],
        "target_id": "t-1",
    }
    async with factory() as db:
        await emit_apikey_added(db, **kw)
        await emit_apikey_removed(db, **kw)
        await emit_invite_minted(db, **kw)
        await emit_invite_redeemed(db, **kw)
        await emit_invite_revoked(db, **kw)
        await emit_invite_resent(db, **kw)  # M14-F2a
        await emit_billing_plan_changed(db, **kw)
        # Mission 22 M4: SSO config lifecycle helpers (sso.py call sites).
        from harness_identity.audit_emit import (
            emit_sso_config_deleted,
            emit_sso_config_saved,
        )

        await emit_sso_config_saved(db, **kw)
        await emit_sso_config_deleted(db, **kw)
        rows = (await db.execute(select(AuditLog))).scalars().all()

    expected = sorted([*REGISTRY_S1_CALLED, "sso.config_saved", "sso.config_deleted"])
    assert sorted(row.action for row in rows) == expected
    for row in rows:
        assert row.actor_member_id == admin["memberId"]
        assert row.detail.get("teamId") == admin["teamId"]


# --------------------------------------------------------- secret scrubbing


def test_scrub_drops_secret_shaped_keys():
    dirty = {
        "label": "prod",
        "apiKey": RAW_KEY,
        "inviteToken": "raw-token-value",
        "password": "hunter2",
        "otpCode": "123456",
        "authorization": "Bearer x",
    }
    clean = _scrub(dirty)
    assert clean == {"label": "prod"}
    assert RAW_KEY not in str(clean)


async def test_helper_detail_scrubbed_even_if_caller_passes_secret(factory, client):
    admin = await _seed_user(factory, "sloppy@x.com")
    async with factory() as db:
        row = await emit_apikey_added(
            db,
            actor_user_id=admin["userId"],
            team_id=admin["teamId"],
            target_id="k-1",
            detail={"label": "oops", "key": RAW_KEY},  # caller error…
        )
    assert row is not None
    assert RAW_KEY not in str(row.detail)  # …but the sink stays secret-free


# --------------------------------------------------- never-throw guarantee


@pytest.fixture
def broken_sink(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("audit sink down")

    monkeypatch.setattr(audit_emit_module, "write_audit", boom)


async def test_helpers_swallow_sink_failure_and_warn(factory, broken_sink, caplog):
    admin = await _seed_user(factory, "dark@x.com")
    with caplog.at_level("WARNING", logger="harness_identity.audit_emit"):
        async with factory() as db:
            out = await emit_apikey_added(
                db,
                actor_user_id=admin["userId"],
                team_id=admin["teamId"],
                target_id="k-9",
            )
            out2 = await emit_run_started(db, run_id="r-9")
    assert out is None and out2 is None  # degraded, NOT raised
    assert any("audit emission failed" in rec.message for rec in caplog.records)


async def test_request_survives_broken_audit_sink(factory, client, broken_sink):
    """HARD RULE: emission failure never throws into the request path."""
    admin = await _seed_user(factory, "alive@x.com")
    r = await client.post(
        "/api/v1/keys",
        json={"label": "still works", "provider": "openai", "key": RAW_KEY},
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 201, r.text
    async with factory() as db:
        keys = (await db.execute(select(ApiKey))).scalars().all()
    assert len(keys) == 1  # business effect intact despite dead sink


# ------------------------------------------------------ append-only guard


async def test_append_only_guard_still_blocks_update_and_delete(factory):
    """The C-002 wiring adds rows but must not weaken the existing backstop."""
    register_append_only_guard()
    admin = await _seed_user(factory, "immutable@x.com")
    async with factory() as db:
        row = await emit_apikey_added(
            db,
            actor_user_id=admin["userId"],
            team_id=admin["teamId"],
            target_id="k-immutable",
        )
        assert row is not None

    async with factory() as db:
        victim = await db.get(AuditLog, row.seq)
        assert victim is not None
        victim.action = "tampered"
        with pytest.raises(RuntimeError, match="append-only"):
            await db.commit()

    async with factory() as db:
        victim = await db.get(AuditLog, row.seq)
        assert victim is not None
        await db.delete(victim)
        with pytest.raises(RuntimeError, match="append-only"):
            await db.commit()
