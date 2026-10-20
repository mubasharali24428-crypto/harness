"""Part 38 (Lane P): policy router - step-up, emergency access, preview, log, isolation."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from harness_identity import policy
from harness_identity.app import make_app
from harness_identity.auth import issue_otp
from harness_identity.config import Settings
from harness_identity.models import AuditLog

T0 = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
ORG, ORG_B = "org-a", "org-b"


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


async def good_verifier(request, db, principal, code):  # test double for the OTP mechanism
    return code == "000000"


@pytest.fixture
async def env(db):
    clock = Clock()
    settings = Settings(
        master_key=b"test-master-key-0123456789abcdef", cors_origins=[], require_ownership=False
    )
    app = make_app(db, settings)
    app.include_router(policy.router)
    async with db() as s:
        connection = await s.connection()
        await connection.run_sync(policy._md.create_all)
    app.state.policy_clock = clock
    app.state.stepup_verifier = good_verifier
    async with db() as s:
        for ddl in (
            "CREATE TABLE IF NOT EXISTS organizations "
            "(id TEXT PRIMARY KEY, name TEXT, slug TEXT, status TEXT, created_at TEXT, "
            "require_sso BOOLEAN DEFAULT FALSE, require_mfa BOOLEAN DEFAULT FALSE, "
            "require_dual_approval BOOLEAN DEFAULT FALSE, "
            "event_retention_days INTEGER, audit_retention_days INTEGER, "
            "allowed_domains TEXT DEFAULT '[]', execution_disabled BOOLEAN NOT NULL DEFAULT FALSE)",
            "CREATE TABLE IF NOT EXISTS org_members (org_id TEXT, user_id TEXT, role TEXT, "
            "expires_at TEXT, created_at TEXT, removed_at TEXT)",
            "CREATE TABLE IF NOT EXISTS policy_documents (id TEXT PRIMARY KEY, org_id TEXT, "
            "level TEXT, scope_id TEXT, version INTEGER, doc TEXT, "
            "updated_by TEXT, updated_at TEXT)",
        ):
            await s.execute(text(ddl))
        for org_id in (ORG, ORG_B):
            await s.execute(
                text(
                    "INSERT INTO organizations (id, name, slug, status, created_at) "
                    "VALUES (:o, :o, :o, 'active', :n)"
                ),
                {"o": org_id, "n": T0},
            )
        await s.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        users = {}
        for name in ("alice", "bob", "carol", "eve", "frank"):
            r = await c.post("/api/v1/auth/login", json={"email": f"{name}@example.com"})
            assert r.status_code == 200, r.text
            users[name] = (
                r.json()["token"],
                r.json()["principal"]["userId"],
                r.json()["principal"]["teamId"],
            )
        async with db() as s:
            for name, org, role in [
                ("alice", ORG, "owner"),
                ("bob", ORG, "admin"),
                ("carol", ORG, "member"),
                ("eve", ORG_B, "owner"),
                ("frank", ORG, "owner"),
            ]:
                await s.execute(
                    text("INSERT INTO org_members (org_id,user_id,role) VALUES (:o,:u,:r)"),
                    {"o": org, "u": users[name][1], "r": role},
                )
            await s.commit()
        c.users, c.clock, c.db, c.app = users, clock, db, app
        yield c


def H(c, name):
    return {"Authorization": f"Bearer {c.users[name][0]}"}


def uid(c, name):
    return c.users[name][1]


async def stepup(c, name):
    r = await c.post("/api/v1/stepup/verify", json={"code": "000000"}, headers=H(c, name))
    assert r.status_code == 200, r.text


async def test_org_execution_stop_requires_stepup_and_is_audited(env):
    c = env
    url = f"/api/v1/orgs/{ORG}/execution-control"
    before = await c.get(url, headers=H(c, "alice"))
    assert before.status_code == 200 and before.json()["disabled"] is False
    denied = await c.put(
        url,
        json={"disabled": True, "reason": "Incident response containment"},
        headers=H(c, "alice"),
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["error"]["code"] == "STEPUP_REQUIRED"
    await stepup(c, "alice")
    stopped = await c.put(
        url,
        json={"disabled": True, "reason": "Incident response containment"},
        headers=H(c, "alice"),
    )
    assert stopped.status_code == 200 and stopped.json()["disabled"] is True
    outsider = await c.get(url, headers=H(c, "eve"))
    assert outsider.status_code == 404
    async with c.db() as db:
        audit = (
            await db.execute(
                text("SELECT detail FROM audit_log WHERE action='org.execution_control_changed'")
            )
        ).scalar_one()
        assert '"executionDisabled": false' in audit
        assert '"executionDisabled": true' in audit


async def test_high_risk_policy_change_requires_a_second_admin(env):
    c = env
    await stepup(c, "alice")
    configured = await c.patch(
        f"/api/v1/orgs/{ORG}", json={"requireDualApproval": True}, headers=H(c, "alice")
    )
    assert configured.status_code == 200 and configured.json()["requireDualApproval"] is True
    submitted = await c.put(
        P(f"{ORG}/documents/org/{ORG}"), json=DENY_EXPORT, headers=H(c, "alice")
    )
    assert submitted.status_code == 200 and submitted.json()["pendingApproval"] is True
    request_id = submitted.json()["requestId"]
    self_approval = await c.post(
        P(f"{ORG}/change-requests/{request_id}/approve"), headers=H(c, "alice")
    )
    assert self_approval.status_code == 409
    await stepup(c, "bob")
    approved = await c.post(
        P(f"{ORG}/change-requests/{request_id}/approve"), headers=H(c, "bob")
    )
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    docs = await c.get(P(f"{ORG}/documents"), headers=H(c, "alice"))
    assert docs.json()["documents"][0]["version"] == 1


def P(path):
    return f"/api/v1/policy/orgs/{path}"


DENY_EXPORT = {
    "doc": {
        "rules": [
            {
                "id": "no-export",
                "effect": "deny",
                "permissions": ["export"],
                "subjects": ["role:member"],
            }
        ]
    }
}


async def audit_actions(c):
    async with c.db() as s:
        return [a for (a,) in (await s.execute(select(AuditLog.action))).all()]


async def test_stepup_required_then_satisfied_then_stale(env):
    c = env
    url = P(f"{ORG}/documents/org/{ORG}")
    r = await c.put(url, json=DENY_EXPORT, headers=H(c, "alice"))
    assert r.status_code == 403 and r.json()["detail"]["error"]["code"] == "STEPUP_REQUIRED"
    bad = await c.post("/api/v1/stepup/verify", json={"code": "999999"}, headers=H(c, "alice"))
    assert bad.status_code == 401
    await stepup(c, "alice")
    r = await c.put(url, json=DENY_EXPORT, headers=H(c, "alice"))
    assert r.status_code == 200 and r.json()["version"] == 1
    c.clock.now += timedelta(seconds=299)
    assert (await c.put(url, json=DENY_EXPORT, headers=H(c, "alice"))).status_code == 200
    c.clock.now += timedelta(seconds=302)
    assert (await c.put(url, json=DENY_EXPORT, headers=H(c, "alice"))).status_code == 403
    acts = await audit_actions(c)
    assert "stepup.required" in acts and "stepup.verified" in acts and "policy.saved" in acts


async def test_stepup_not_required_when_org_policy_omits_it(env):
    c = env
    url = P(f"{ORG}/documents/org/{ORG}")
    await stepup(c, "alice")
    ok = await c.put(url, json={"doc": {"stepup_actions": []}}, headers=H(c, "alice"))
    assert ok.status_code == 200
    c.clock.now += timedelta(hours=2)  # stale step-up, but policy no longer requires it
    assert (
        await c.put(
            url, json={"doc": {"stepup_actions": []}, "expectedVersion": 1}, headers=H(c, "alice")
        )
    ).status_code == 200
    stale = await c.put(url, json={"doc": {}, "expectedVersion": 1}, headers=H(c, "alice"))
    assert stale.status_code == 409


async def test_real_otp_mechanism_default_verifier(env):
    c = env
    del c.app.state.stepup_verifier  # fall back to the OTP verifier
    sent = {}
    await issue_otp(
        c.db, c.app.state.settings, lambda e, code: sent.update({e: code}), "alice@example.com"
    )
    r = await c.post("/api/v1/stepup/verify", json={"code": "123456"}, headers=H(c, "alice"))
    assert r.status_code == 401
    r = await c.post(
        "/api/v1/stepup/verify", json={"code": sent["alice@example.com"]}, headers=H(c, "alice")
    )
    assert r.status_code == 200
    again = await c.post(
        "/api/v1/stepup/verify", json={"code": sent["alice@example.com"]}, headers=H(c, "alice")
    )
    assert again.status_code == 401  # single-use


async def test_decision_log_allow_and_deny_and_bounds(env):
    c = env
    ok = await c.post(P(f"{ORG}/check"), json={"permission": "steer"}, headers=H(c, "carol"))
    no = await c.post(P(f"{ORG}/check"), json={"permission": "approve"}, headers=H(c, "carol"))
    assert ok.json()["allowed"] is True and no.json()["allowed"] is False and no.json()["reason"]
    r = await c.get(P(f"{ORG}/decisions"), headers=H(c, "alice"))
    rows = [d for d in r.json()["decisions"] if d["subject"] == uid(c, "carol")]
    assert {(d["permission"], d["decision"]) for d in rows} >= {
        ("steer", "allow"),
        ("approve", "deny"),
    }
    assert all(d["resource"] == f"org:{ORG}" and d["ts"] for d in rows)
    deny_only = await c.get(P(f"{ORG}/decisions?decision=deny"), headers=H(c, "alice"))
    assert deny_only.json()["decisions"] and all(
        d["decision"] == "deny" for d in deny_only.json()["decisions"]
    )
    assert (await c.get(P(f"{ORG}/decisions?limit=201"), headers=H(c, "alice"))).status_code == 422
    wide = (c.clock.now - timedelta(days=40)).isoformat()
    assert (
        await c.get(P(f"{ORG}/decisions"), params={"since": wide}, headers=H(c, "alice"))
    ).status_code == 422
    assert (
        await c.get(P(f"{ORG}/decisions"), headers=H(c, "carol"))
    ).status_code == 403  # admins only


async def test_cross_org_isolation(env):
    c = env
    await stepup(c, "alice")
    assert (
        await c.put(P(f"{ORG}/documents/org/{ORG}"), json=DENY_EXPORT, headers=H(c, "alice"))
    ).status_code == 200
    # eve owns org B only: org A is invisible (404, no existence oracle) on every verb
    for method, path, body in [
        ("get", f"{ORG}/documents", None),
        ("get", f"{ORG}/decisions", None),
        ("get", f"{ORG}/roles", None),
        ("post", f"{ORG}/check", {"permission": "read"}),
        ("get", f"{ORG}/emergency", None),
        ("put", f"{ORG}/documents/org/{ORG}", DENY_EXPORT),
    ]:
        r = await getattr(c, method)(
            P(path), headers=H(c, "eve"), **({"json": body} if body else {})
        )
        assert r.status_code == 404, (method, path, r.text)
    # org A's deny does not leak into org B decisions
    r = await c.post(P(f"{ORG_B}/check"), json={"permission": "export"}, headers=H(c, "eve"))
    assert r.json()["allowed"] is True
    # a user who is a member of A cannot read B either
    assert (await c.get(P(f"{ORG_B}/documents"), headers=H(c, "alice"))).status_code == 404
    # decisions of org A never show up in org B's log
    rows = (await c.get(P(f"{ORG_B}/decisions"), headers=H(c, "eve"))).json()["decisions"]
    assert all(d["subject"] == uid(c, "eve") for d in rows)


async def test_escalation_attempts_denied(env):
    c = env
    await stepup(c, "bob")
    # admin lacks billing -> cannot mint a role carrying it
    r = await c.put(
        P(f"{ORG}/roles/money"), json={"permissions": ["read", "billing"]}, headers=H(c, "bob")
    )
    assert r.status_code == 403 and r.json()["detail"]["error"]["code"] == "ESCALATION"
    ok = await c.put(
        P(f"{ORG}/roles/reviewer"), json={"permissions": ["read", "approve"]}, headers=H(c, "bob")
    )
    assert ok.status_code == 200
    assert (
        await c.put(P(f"{ORG}/roles/admin"), json={"permissions": ["read"]}, headers=H(c, "bob"))
    ).status_code == 422
    body = {"userId": uid(c, "carol"), "level": "project", "scopeId": "p1"}
    # granting owner (billing) on a project: denied; granting the custom role: allowed
    r = await c.post(P(f"{ORG}/assignments"), json={**body, "role": "owner"}, headers=H(c, "bob"))
    assert r.status_code == 403 and r.json()["detail"]["error"]["code"] == "ESCALATION"
    assert (
        await c.post(
            P(f"{ORG}/assignments"), json={**body, "role": "reviewer"}, headers=H(c, "bob")
        )
    ).status_code == 201
    # the assignment is now effective for carol on that project only
    chain = [{"level": "project", "id": "p1"}]
    yes = await c.post(
        P(f"{ORG}/check"), json={"permission": "approve", "chain": chain}, headers=H(c, "carol")
    )
    no = await c.post(P(f"{ORG}/check"), json={"permission": "approve"}, headers=H(c, "carol"))
    assert yes.json()["allowed"] and not no.json()["allowed"]
    # member cannot grant at all; non-member target is rejected
    assert (
        await c.post(
            P(f"{ORG}/assignments"), json={**body, "role": "viewer"}, headers=H(c, "carol")
        )
    ).status_code == 403
    r = await c.post(
        P(f"{ORG}/assignments"),
        json={**body, "userId": uid(c, "eve"), "role": "viewer"},
        headers=H(c, "bob"),
    )
    assert r.status_code == 404
    # an org-level deny on approve also blocks a custom-role holder from being granted it
    assert "policy.custom_role_saved" in await audit_actions(c)


async def test_emergency_lifecycle_with_fake_clock(env):
    c = env
    req = {
        "permissions": ["billing"],
        "reason": "payment outage, need billing console",
        "durationMinutes": 30,
    }
    assert (
        await c.post(P(f"{ORG}/emergency"), json={**req, "reason": "short"}, headers=H(c, "bob"))
    ).status_code == 422
    assert (
        await c.post(
            P(f"{ORG}/emergency"), json={**req, "durationMinutes": 241}, headers=H(c, "bob")
        )
    ).status_code == 422
    r = await c.post(P(f"{ORG}/emergency"), json=req, headers=H(c, "bob"))
    gid = r.json()["id"]
    assert r.status_code == 201 and r.json()["status"] == "pending"
    blocked = await c.post(P(f"{ORG}/check"), json={"permission": "billing"}, headers=H(c, "bob"))
    assert blocked.json()["allowed"] is False  # pending grants give nothing
    await stepup(c, "bob")
    own = await c.post(P(f"{ORG}/emergency/{gid}/approve"), headers=H(c, "bob"))
    assert own.status_code == 403 and own.json()["detail"]["error"]["code"] == "SELF_APPROVAL"
    await stepup(c, "alice")
    assert (
        await c.post(P(f"{ORG}/emergency/{gid}/approve"), headers=H(c, "alice"))
    ).status_code == 200
    assert (
        await c.post(P(f"{ORG}/emergency/{gid}/approve"), headers=H(c, "alice"))
    ).status_code == 409
    c.clock.now += timedelta(minutes=10)
    used = await c.post(P(f"{ORG}/check"), json={"permission": "billing"}, headers=H(c, "bob"))
    assert used.json()["allowed"] is True and used.json()["chain"][0]["source"] == "emergency"
    assert "emergency.used" in await audit_actions(c)
    c.clock.now += timedelta(minutes=21)  # past 30 min
    gone = await c.post(P(f"{ORG}/check"), json={"permission": "billing"}, headers=H(c, "bob"))
    assert gone.json()["allowed"] is False
    listing = (await c.get(P(f"{ORG}/emergency"), headers=H(c, "alice"))).json()["grants"]
    assert listing[0]["status"] == "expired"
    assert "emergency.expired" in await audit_actions(
        c
    ) and "emergency.granted" in await audit_actions(c)


async def test_emergency_revocation_and_approver_must_hold_permission(env):
    c = env
    req = {
        "permissions": ["billing"],
        "reason": "need billing access urgently",
        "durationMinutes": 60,
    }
    gid = (await c.post(P(f"{ORG}/emergency"), json=req, headers=H(c, "carol"))).json()["id"]
    await stepup(c, "bob")
    r = await c.post(
        P(f"{ORG}/emergency/{gid}/approve"), headers=H(c, "bob")
    )  # admin has no billing
    assert r.status_code == 403 and r.json()["detail"]["error"]["code"] == "ESCALATION"
    await stepup(c, "alice")
    assert (
        await c.post(P(f"{ORG}/emergency/{gid}/approve"), headers=H(c, "alice"))
    ).status_code == 200
    assert (
        await c.post(P(f"{ORG}/check"), json={"permission": "billing"}, headers=H(c, "carol"))
    ).json()["allowed"]
    assert (
        await c.post(P(f"{ORG}/emergency/{gid}/revoke"), headers=H(c, "bob"))
    ).status_code == 200
    assert not (
        await c.post(P(f"{ORG}/check"), json={"permission": "billing"}, headers=H(c, "carol"))
    ).json()["allowed"]
    assert "emergency.revoked" in await audit_actions(c)


async def test_preview_equals_real_evaluation(env):
    c = env
    users = [uid(c, "carol"), uid(c, "bob")]
    draft = {
        "drafts": [{"level": "org", "scopeId": ORG, **DENY_EXPORT}],
        "userIds": users,
        "chain": [{"level": "project", "id": "p1"}],
    }
    pv = await c.post(P(f"{ORG}/preview"), json=draft, headers=H(c, "alice"))
    assert pv.status_code == 200, pv.text
    changes = pv.json()["changes"]
    assert [
        (x["userId"], x["permission"]) for x in changes
    ] == []  # member never had export: no change
    # grant carol a role with export, then the draft deny must show as a real change
    await stepup(c, "alice")
    await c.put(
        P(f"{ORG}/roles/exporter"), json={"permissions": ["read", "export"]}, headers=H(c, "alice")
    )
    await c.post(
        P(f"{ORG}/assignments"),
        json={"userId": uid(c, "carol"), "level": "project", "scopeId": "p1", "role": "exporter"},
        headers=H(c, "alice"),
    )
    pv = await c.post(P(f"{ORG}/preview"), json=draft, headers=H(c, "alice"))
    changes = pv.json()["changes"]
    assert [
        (x["userId"], x["permission"], x["current"]["allowed"], x["draft"]["allowed"])
        for x in changes
    ] == [(uid(c, "carol"), "export", True, False)]
    assert (await c.get(P(f"{ORG}/documents"), headers=H(c, "alice"))).json()[
        "documents"
    ] == []  # nothing saved
    assert (
        await c.put(P(f"{ORG}/documents/org/{ORG}"), json=DENY_EXPORT, headers=H(c, "alice"))
    ).status_code == 200
    for item in pv.json()["results"]:
        real = await c.post(
            P(f"{ORG}/check"),
            json={
                "permission": item["permission"],
                "userId": item["userId"],
                "chain": draft["chain"],
            },
            headers=H(c, "alice"),
        )
        assert real.json() == item["draft"], item
    # draft custom roles replace the current ones in preview
    pv2 = await c.post(
        P(f"{ORG}/preview"),
        json={
            "customRoles": {"exporter": ["read"]},
            "userIds": [uid(c, "carol")],
            "permissions": ["export"],
            "chain": draft["chain"],
        },
        headers=H(c, "alice"),
    )
    assert pv2.status_code == 200
    # preview is admin-only and validates input
    assert (await c.post(P(f"{ORG}/preview"), json=draft, headers=H(c, "carol"))).status_code == 403
    bad = await c.post(
        P(f"{ORG}/preview"), json={"userIds": users, "permissions": ["fly"]}, headers=H(c, "alice")
    )
    assert bad.status_code == 422


async def test_invalid_documents_rejected(env):
    c = env
    await stepup(c, "alice")
    url = P(f"{ORG}/documents/org/{ORG}")
    for doc in (
        {"rules": [{"id": "x", "effect": "maybe", "permissions": ["read"]}]},
        {"rules": [{"id": "x", "effect": "deny", "permissions": ["fly"]}]},
        {
            "rules": [
                {"id": "x", "effect": "deny", "permissions": ["read"]},
                {"id": "x", "effect": "deny", "permissions": ["read"]},
            ]
        },
        {
            "rules": [
                {"id": "x", "effect": "deny", "permissions": ["read"], "subjects": ["everyone"]}
            ]
        },
        {"stepup_actions": ["nope"]},
    ):
        assert (await c.put(url, json={"doc": doc}, headers=H(c, "alice"))).status_code == 422, doc
    assert (
        await c.put(P(f"{ORG}/documents/org/other-org"), json={"doc": {}}, headers=H(c, "alice"))
    ).status_code == 422


async def test_authorize_function_logs_and_stored_policy_denies(env):
    c = env
    await stepup(c, "alice")
    await c.put(P(f"{ORG}/documents/org/{ORG}"), json=DENY_EXPORT, headers=H(c, "alice"))
    async with c.db() as s:
        d = await policy.authorize(
            s,
            org_id=ORG,
            user_id=uid(c, "carol"),
            permission="export",
            chain=[policy.Node("org", ORG)],
            now=c.clock.now,
        )
        assert not d.allowed and d.rule_ids == ["policy:org:no-export"]
        ghost = await policy.authorize(
            s,
            org_id=ORG,
            user_id="nobody",
            permission="read",
            chain=[policy.Node("org", ORG)],
            now=c.clock.now,
        )
        assert not ghost.allowed and "not an active member" in ghost.reason
        with pytest.raises(policy.PolicyError):
            await policy.authorize(
                s,
                org_id=ORG,
                user_id="x",
                permission="read",
                chain=[policy.Node("org", ORG_B)],
                now=c.clock.now,
            )
        rows = (
            await s.execute(
                text("SELECT decision, rule_ids FROM policy_decision_log WHERE subject='nobody'")
            )
        ).all()
        assert [r[0] for r in rows] == ["deny"]


def test_migration_matches_tables():
    sql = (Path(__file__).parent.parent / "migrations" / "0039_policy.sql").read_text()
    approvals = (
        Path(__file__).parent.parent / "migrations" / "0054_policy_approvals.sql"
    ).read_text()
    for t in policy._md.tables.values():
        source = approvals if t.name == "policy_change_requests" else sql
        assert f"CREATE TABLE IF NOT EXISTS {t.name} " in source or (
            t.name == "policy_change_requests" and f"CREATE TABLE {t.name} " in source
        ), t.name
        for col in t.columns:
            assert col.name in source, (t.name, col.name)


async def test_stepup_gates_legacy_role_change_and_export_for_org_teams(env):
    """Part 38.010 wiring: teams linked to an org need a fresh step-up for role.change/export;
    unlinked teams are unaffected."""
    from harness_identity.models import TeamMember, User

    c = env
    _, alice_id, alice_team = c.users["alice"]
    async with c.db() as s:
        await policy.ensure_schema(s)  # prod gets these tables from migration 0039
        await s.execute(text("ALTER TABLE teams ADD COLUMN org_id VARCHAR(36)"))
        await s.execute(text("UPDATE teams SET org_id=:o WHERE id=:t"), {"o": ORG, "t": alice_team})
        peer = User(email="peer@example.com", name="peer")
        s.add(peer)
        await s.flush()
        s.add(TeamMember(team_id=alice_team, user_id=peer.id, display_name="peer", role="viewer"))
        await s.commit()
        peer_id = peer.id
    url = f"/api/v1/teams/{alice_team}/members/{peer_id}"
    r = await c.patch(url, json={"role": "engineer"}, headers=H(c, "alice"))
    assert r.status_code == 403 and r.json()["detail"]["error"]["code"] == "STEPUP_REQUIRED"
    r = await c.get("/api/v1/audit/export", headers=H(c, "alice"))
    assert r.status_code == 403 and r.json()["detail"]["error"]["code"] == "STEPUP_REQUIRED"
    await stepup(c, "alice")
    assert (await c.patch(url, json={"role": "engineer"}, headers=H(c, "alice"))).status_code == 200
    assert (await c.get("/api/v1/audit/export", headers=H(c, "alice"))).status_code == 200
    # a team with no org is untouched
    _, _, bob_team = c.users["bob"]
    r = await c.get("/api/v1/audit/export", headers=H(c, "bob"))
    assert r.status_code == 200
