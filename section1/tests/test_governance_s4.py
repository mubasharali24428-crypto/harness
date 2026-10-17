"""A1-S4 — package trust, SCIM, correlation, remediation, simulator, retention (offline)."""

import base64
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.governance_api import router as gov_router
from harness_identity.model_policy_api import router as mp_router
from harness_identity.models import (
    AgentEvent,
    AuthSession,
    Base,
    Project,
    Team,
    TeamMember,
    User,
)
from harness_identity.package_trust import (
    IssuerKey,
    public_key_b64,
    sha256_hex,
    sign_manifest,
    verify_package,
)
from harness_identity.scim_api import router as scim_router
from harness_identity.security_ops import correlate, propose_remediation

P = "/api/v1/identity"
S = f"{P}/scim/v2"


@pytest.fixture
async def env():
    from harness_identity.auth import get_principal  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    for r in (gov_router, mp_router, scim_router):
        app.include_router(r)
    app.state.session_factory = factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        yield client, factory
    await engine.dispose()


async def _member(factory, team_id=None, role="admin", email=None):
    async with factory() as db:
        if team_id is None:
            team = Team(name="t")
            db.add(team)
            await db.flush()
            team_id = team.id
        n = secrets.token_hex(3)
        user = User(email=email or f"{n}@x.io", name=n)
        db.add(user)
        await db.flush()
        db.add(TeamMember(team_id=team_id, user_id=user.id, display_name=n, role=role))
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
        return {"team": team_id, "id": user.id, "h": {"Authorization": f"Bearer {token}"}}


# ----------------------------------------------------------- package trust (pure)

KEY = Ed25519PrivateKey.generate()
DATA = b"print('hello plugin')"


def _manifest(**kw):
    m = {
        "name": "plug",
        "version": "1.0.0",
        "sha256": sha256_hex(DATA),
        "issuer": "acme",
        "keyId": "k1",
        "issuedAt": "2026-01-01T00:00:00+00:00",
    }
    return {**m, **kw}


def _trust(**kw):
    return {"k1": IssuerKey("acme", public_key_b64(KEY), kw.get("active", True))}


def test_valid_signature_trusted_and_every_tamper_rejected():
    m = _manifest()
    sig = sign_manifest(KEY, m)
    ok = verify_package(m, sig, content_sha256=sha256_hex(DATA), issuers=_trust())
    assert ok["trusted"] and ok["reason"] == "package.verified"
    cases = {
        "package.digest_mismatch": dict(
            m=m, sig=sig, digest=sha256_hex(DATA + b"x")
        ),  # substituted bytes
        "package.bad_signature": dict(m=_manifest(version="9.9.9"), sig=sig),  # version swap
        "package.issuer_mismatch": dict(
            m=_manifest(issuer="evil"), sig=sign_manifest(KEY, _manifest(issuer="evil"))
        ),
        "package.unknown_key": dict(m=_manifest(keyId="nope"), sig=sig),
        "package.malformed": dict(m={"name": "x"}, sig=sig),
    }
    for reason, c in cases.items():
        r = verify_package(
            c["m"], c["sig"], content_sha256=c.get("digest", sha256_hex(DATA)), issuers=_trust()
        )
        assert (r["trusted"], r["reason"]) == (False, reason), reason
    other = Ed25519PrivateKey.generate()
    forged = verify_package(
        m, sign_manifest(other, m), content_sha256=sha256_hex(DATA), issuers=_trust()
    )
    assert forged["reason"] == "package.bad_signature"
    assert (
        verify_package(m, "!!!", content_sha256=sha256_hex(DATA), issuers=_trust())["reason"]
        == "package.bad_signature"
    )
    assert (
        verify_package(m, sig, content_sha256=sha256_hex(DATA), issuers=_trust(active=False))[
            "reason"
        ]
        == "package.issuer_revoked"
    )
    assert (
        verify_package(
            m,
            sig,
            content_sha256=sha256_hex(DATA),
            issuers=_trust(),
            revoked_name_versions={("plug", "1.0.0")},
        )["reason"]
        == "package.revoked"
    )
    assert (
        verify_package(
            m,
            sig,
            content_sha256=sha256_hex(DATA),
            issuers=_trust(),
            revoked_digests={sha256_hex(DATA)},
        )["reason"]
        == "package.revoked"
    )
    exp = _manifest(notAfter="2026-02-01T00:00:00+00:00")
    r = verify_package(
        exp,
        sign_manifest(KEY, exp),
        content_sha256=sha256_hex(DATA),
        issuers=_trust(),
        now=datetime(2026, 3, 1, tzinfo=UTC),
    )
    assert r["reason"] == "package.expired"


async def test_trust_store_http_flow(env):
    client, factory = env
    admin = await _member(factory)
    eng = await _member(factory, admin["team"], "engineer")
    body = {"keyId": "k1", "issuer": "acme", "publicKey": public_key_b64(KEY)}
    assert (
        await client.post(f"{P}/package-trust/issuers", headers=eng["h"], json=body)
    ).status_code == 403
    bad = await client.post(
        f"{P}/package-trust/issuers", headers=admin["h"], json={**body, "publicKey": "AAAA"}
    )
    assert bad.status_code == 422
    assert (
        await client.post(f"{P}/package-trust/issuers", headers=admin["h"], json=body)
    ).status_code == 201
    assert (
        await client.post(f"{P}/package-trust/issuers", headers=admin["h"], json=body)
    ).status_code == 409
    m = _manifest()
    v = {
        "manifest": m,
        "signature": sign_manifest(KEY, m),
        "contentBase64": base64.b64encode(DATA).decode(),
    }
    r = (await client.post(f"{P}/package-trust/verify", headers=eng["h"], json=v)).json()
    assert r["trusted"] and r["bytesBound"] is True
    swapped = {**v, "contentBase64": base64.b64encode(b"malware").decode()}
    assert (await client.post(f"{P}/package-trust/verify", headers=eng["h"], json=swapped)).json()[
        "reason"
    ] == "package.digest_mismatch"
    # digest-only mode is explicitly NOT byte-bound
    d = {"manifest": m, "signature": v["signature"], "contentSha256": sha256_hex(DATA)}
    assert (await client.post(f"{P}/package-trust/verify", headers=eng["h"], json=d)).json()[
        "bytesBound"
    ] is False
    # another team's trust store does not trust this issuer
    other = await _member(factory)
    assert (await client.post(f"{P}/package-trust/verify", headers=other["h"], json=v)).json()[
        "reason"
    ] == "package.unknown_key"
    # revoke package, then issuer
    rv = await client.post(
        f"{P}/package-trust/revocations",
        headers=admin["h"],
        json={"name": "plug", "version": "1.0.0"},
    )
    assert rv.status_code == 201
    assert (await client.post(f"{P}/package-trust/verify", headers=eng["h"], json=v)).json()[
        "reason"
    ] == "package.revoked"
    await client.post(f"{P}/package-trust/issuers/k1/revoke", headers=admin["h"])
    assert (await client.post(f"{P}/package-trust/verify", headers=eng["h"], json=v)).json()[
        "reason"
    ] == "package.issuer_revoked"
    assert (
        await client.post(f"{P}/package-trust/issuers/k1/revoke", headers=other["h"])
    ).status_code == 404


# ----------------------------------------------------------- SCIM


async def _scim(client, admin):
    r = await client.post(f"{P}/scim/tokens", headers=admin["h"])
    assert r.status_code == 201
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["id"]


async def test_scim_requires_valid_token_and_admin_to_mint(env):
    client, factory = env
    admin = await _member(factory)
    eng = await _member(factory, admin["team"], "engineer")
    assert (await client.post(f"{P}/scim/tokens", headers=eng["h"])).status_code == 403
    assert (await client.get(f"{S}/Users")).status_code == 401
    assert (
        await client.get(f"{S}/Users", headers={"Authorization": "Bearer nope"})
    ).status_code == 401
    h, tid = await _scim(client, admin)
    assert (await client.get(f"{S}/Users", headers=h)).status_code == 200
    assert (await client.delete(f"{P}/scim/tokens/{tid}", headers=admin["h"])).status_code == 204
    assert (await client.get(f"{S}/Users", headers=h)).status_code == 401  # revoked token dead
    listing = (await client.get(f"{P}/scim/tokens", headers=admin["h"])).text
    assert "scim_" not in listing  # secret never listed


async def test_scim_provision_roles_and_escalation_denied(env):
    client, factory = env
    admin = await _member(factory)
    h, _ = await _scim(client, admin)
    r = await client.post(
        f"{S}/Users", headers=h, json={"userName": "New@Corp.com", "displayName": "New"}
    )
    assert r.status_code == 201 and r.json()["userName"] == "new@corp.com"
    assert r.json()["roles"] == [{"value": "viewer"}] and r.json()["active"] is True
    uid = r.json()["id"]
    assert (
        await client.post(f"{S}/Users", headers=h, json={"userName": "new@corp.com"})
    ).status_code == 409
    for role in ("admin", "owner", "guest"):
        e = await client.post(
            f"{S}/Users",
            headers=h,
            json={"userName": f"{role}@corp.com", "roles": [{"value": role}]},
        )
        assert e.status_code == 400 and e.json()["scimType"] == "invalidValue", role
    up = await client.patch(
        f"{S}/Users/{uid}",
        headers=h,
        json={"Operations": [{"op": "replace", "path": "roles", "value": [{"value": "engineer"}]}]},
    )
    assert up.json()["roles"] == [{"value": "engineer"}]
    esc = await client.patch(
        f"{S}/Users/{uid}",
        headers=h,
        json={"Operations": [{"op": "replace", "path": "roles", "value": [{"value": "admin"}]}]},
    )
    assert esc.status_code == 400
    assert (
        await client.post(f"{S}/Users", headers=h, json={"userName": "not-an-email"})
    ).status_code == 400
    flt = await client.get(f"{S}/Users", headers=h, params={"filter": 'userName eq "new@corp.com"'})
    assert flt.json()["totalResults"] == 1
    assert (
        await client.get(f"{S}/Users", headers=h, params={"filter": "userName co x"})
    ).status_code == 400


async def test_scim_cannot_touch_other_tenants_or_edit_admins(env):
    client, factory = env
    a = await _member(factory)
    b = await _member(factory)
    ha, _ = await _scim(client, a)
    assert (await client.get(f"{S}/Users/{b['id']}", headers=ha)).status_code == 404
    assert (
        await client.patch(
            f"{S}/Users/{b['id']}",
            headers=ha,
            json={"Operations": [{"op": "replace", "path": "active", "value": False}]},
        )
    ).status_code == 404
    assert (await client.delete(f"{S}/Users/{b['id']}", headers=ha)).status_code == 404
    # b is untouched and still authenticates
    assert (await client.get(f"{P}/model-allowlist", headers=b["h"])).status_code == 200
    # in-team admin: role is immutable via SCIM
    peer = await _member(factory, a["team"], "admin")
    r = await client.patch(
        f"{S}/Users/{peer['id']}",
        headers=ha,
        json={"Operations": [{"op": "replace", "path": "roles", "value": [{"value": "viewer"}]}]},
    )
    assert r.status_code == 403


async def test_scim_deprovision_kills_session_keeps_audit_row_and_reactivates(env):
    client, factory = env
    admin = await _member(factory)
    eng = await _member(factory, admin["team"], "engineer")
    h, _ = await _scim(client, admin)
    assert (await client.get(f"{P}/model-allowlist", headers=eng["h"])).status_code == 200
    r = await client.patch(
        f"{S}/Users/{eng['id']}",
        headers=h,
        json={"Operations": [{"op": "replace", "path": "active", "value": False}]},
    )
    assert r.json()["active"] is False
    assert (
        await client.get(f"{P}/model-allowlist", headers=eng["h"])
    ).status_code == 401  # offboarded
    async with factory() as db:
        assert (
            await db.execute(select(AuthSession).where(AuthSession.user_id == eng["id"]))
        ).first() is None
        assert (await db.get(User, eng["id"])).is_active is True  # global account untouched
        assert (
            (await db.execute(select(TeamMember).where(TeamMember.user_id == eng["id"])))
            .scalar_one()
            .deactivated_at
        )
    back = await client.patch(
        f"{S}/Users/{eng['id']}",
        headers=h,
        json={"Operations": [{"op": "replace", "path": "active", "value": True}]},
    )
    assert back.json()["active"] is True and back.json()["roles"] == [{"value": "engineer"}]
    assert (await client.delete(f"{S}/Users/{eng['id']}", headers=h)).status_code == 204
    assert (
        await client.delete(f"{S}/Users/{eng['id']}", headers=h)
    ).status_code == 204  # idempotent


async def test_scim_last_admin_protection(env):
    client, factory = env
    admin = await _member(factory)
    h, _ = await _scim(client, admin)
    r = await client.delete(f"{S}/Users/{admin['id']}", headers=h)
    assert r.status_code == 409 and r.json()["scimType"] == "mutability"
    assert (await client.get(f"{P}/model-allowlist", headers=admin["h"])).status_code == 200
    second = await _member(factory, admin["team"], "admin")
    assert (await client.delete(f"{S}/Users/{admin['id']}", headers=h)).status_code == 204
    # now `second` is the last ACTIVE admin
    assert (await client.delete(f"{S}/Users/{second['id']}", headers=h)).status_code == 409


async def test_deactivated_admin_does_not_count_and_removed_member_session_is_401(env):
    client, factory = env
    admin = await _member(factory)
    async with factory() as db:  # membership row deleted entirely (pre-existing removal path)
        m = (
            await db.execute(select(TeamMember).where(TeamMember.user_id == admin["id"]))
        ).scalar_one()
        await db.delete(m)
        await db.commit()
    assert (
        await client.get(f"{P}/model-allowlist", headers=admin["h"])
    ).status_code == 401  # was a 500


# ----------------------------------------------------------- correlation / remediation (pure)


def _ev(i, action, actor, minutes):
    return {
        "id": str(i),
        "action": action,
        "actor": actor,
        "ts": datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=minutes),
    }


def test_correlation_rules_and_negatives():
    burst = [_ev(i, "policy.denied", "m1", i) for i in range(5)]
    assert [f["rule"] for f in correlate(burst)] == ["repeated_denials"]
    spread = [_ev(i, "policy.denied", "m1", i * 20) for i in range(5)]
    assert correlate(spread) == []  # same count, too slow
    mixed = [_ev(i, "policy.denied", "m1", i) for i in range(3)] + [
        _ev(9, "apikey.added", "m1", 10)
    ]
    f = correlate(mixed)
    assert f[0]["rule"] == "denials_then_credential_change" and f[0]["severity"] == "critical"
    assert "9" in f[0]["evidenceIds"]
    two_actors = [_ev(i, "policy.denied", f"m{i % 2}", i) for i in range(6)]
    assert correlate(two_actors) == []  # denials are per actor
    revs = [_ev(i, "apikey.removed", "m2", i) for i in range(5)]
    assert correlate(revs)[0]["rule"] == "mass_revocation"
    assert correlate([]) == []


def test_remediation_proposes_never_applies():
    out = propose_remediation(
        {"Django": "3.2.0", "requests": "2.31.0", "pkg_x": "1.0"},
        [
            {"id": "A1", "package": "django", "fixedIn": "3.2.5", "severity": "high"},
            {"id": "A2", "package": "django", "fixedIn": "4.2.1", "severity": "critical"},
            {"id": "A3", "package": "requests", "fixedIn": "2.31.0"},  # already fixed
            {"id": "A4", "package": "pkg-x", "fixedIn": "not-a-version"},
        ],
    )
    dj = next(p for p in out if p.get("package") == "Django")
    assert dj["to"] == "4.2.1" and dj["severity"] == "critical" and dj["advisories"] == ["A1", "A2"]
    assert (
        dj["autoApply"] is False
        and "manual_review" in dj["gates"]
        and "major_upgrade_review" in dj["gates"]
    )
    assert not any(p.get("package") == "requests" for p in out)
    assert any(p["status"] == "unresolved" for p in out)


async def test_correlation_endpoint_scopes_to_team(env):
    client, factory = env
    a = await _member(factory)
    b = await _member(factory)
    async with factory() as db:
        from harness_identity.audit import write_audit

        ma = (
            await db.execute(select(TeamMember.id).where(TeamMember.user_id == a["id"]))
        ).scalar_one()
        for _ in range(5):
            await write_audit(db, "policy.denied", actor_member_id=ma, detail={"teamId": a["team"]})
    ra = (await client.get(f"{P}/security/correlations", headers=a["h"])).json()
    assert [f["rule"] for f in ra["findings"]] == ["repeated_denials"]
    assert (await client.get(f"{P}/security/correlations", headers=b["h"])).json()["findings"] == []


# ----------------------------------------------------------- simulator


async def test_simulator_composes_checks_without_mutating(env):
    client, factory = env
    admin = await _member(factory)
    body = {
        "role": "viewer",
        "action": "write",
        "text": "key sk-ant-api03-" + "Q" * 40,
        "url": "http://169.254.169.254/",
        "path": ".env",
    }
    r = (await client.post(f"{P}/policy/simulate", headers=admin["h"], json=body)).json()
    got = {c["check"]: c["outcome"] for c in r["checks"]}
    assert got["rbac"] == "deny" and got["outbound_content"] == "redact"
    assert got["egress"] == "deny" and got["sensitive_file"] == "deny"
    assert r["overall"] == "deny" and r["mutated"] is False
    ok = (
        await client.post(
            f"{P}/policy/simulate",
            headers=admin["h"],
            json={"role": "engineer", "action": "approve"},
        )
    ).json()
    assert ok["overall"] == "allow" and len(ok["checks"]) == 1
    assert (
        await client.post(
            f"{P}/policy/simulate", headers=admin["h"], json={"role": "root", "action": "read"}
        )
    ).status_code == 422
    # model check follows the team's enforced allowlist
    await client.put(f"{P}/model-policy", headers=admin["h"], json={"allowlistEnforced": True})
    m = (
        await client.post(f"{P}/policy/simulate", headers=admin["h"], json={"model": "a/x"})
    ).json()
    assert m["checks"][0]["reason"] == "model.not_approved"
    async with factory() as db:
        from harness_identity.models import AuditLog

        acts = [r.action for r in (await db.execute(select(AuditLog))).scalars()]
    assert "policy.denied" not in acts  # simulation writes no denial records


# ----------------------------------------------------------- retention


async def _seed_events(factory, team_id, old=3, new=2, days=40):
    async with factory() as db:
        proj = Project(team_id=team_id, name="p")
        db.add(proj)
        await db.flush()
        for i in range(old + new):
            ts = datetime.now(UTC) - timedelta(days=days if i < old else 1)
            db.add(AgentEvent(project_id=proj.id, type="t", payload={}, ts=ts))
        await db.commit()
        return proj.id


async def test_retention_plan_apply_hold_and_confirm(env):
    client, factory = env
    admin = await _member(factory)
    eng = await _member(factory, admin["team"], "engineer")
    pid = await _seed_events(factory, admin["team"])
    assert (
        await client.put(f"{P}/retention", headers=eng["h"], json={"retainDays": 30})
    ).status_code == 403
    assert (
        await client.put(f"{P}/retention", headers=admin["h"], json={"retainDays": 3})
    ).status_code == 422
    assert (
        await client.post(
            f"{P}/retention/apply", headers=admin["h"], json={"confirm": True, "expectedCount": 0}
        )
    ).status_code == 200  # no policy => nothing eligible
    await client.put(f"{P}/retention", headers=admin["h"], json={"retainDays": 30})
    plan = (await client.post(f"{P}/retention/plan", headers=admin["h"])).json()
    assert plan["willDelete"] == 3
    async with factory() as db:
        assert (
            len((await db.execute(select(AgentEvent))).scalars().all()) == 5
        )  # plan deleted nothing
    assert (
        await client.post(
            f"{P}/retention/apply", headers=admin["h"], json={"confirm": False, "expectedCount": 3}
        )
    ).status_code == 422
    assert (
        await client.post(
            f"{P}/retention/apply", headers=admin["h"], json={"confirm": True, "expectedCount": 99}
        )
    ).status_code == 409
    # legal hold: shortening refused, apply skips
    await client.put(
        f"{P}/retention",
        headers=admin["h"],
        json={"retainDays": 30, "legalHold": True, "holdReason": "case 42"},
    )
    short = await client.put(f"{P}/retention", headers=admin["h"], json={"retainDays": 7})
    assert short.status_code == 409 and short.json()["detail"]["error"]["code"] == "LEGAL_HOLD"
    held = (
        await client.post(
            f"{P}/retention/apply", headers=admin["h"], json={"confirm": True, "expectedCount": 0}
        )
    ).json()
    assert held["deleted"] == 0 and held["skippedHeld"] == [pid]
    async with factory() as db:
        assert len((await db.execute(select(AgentEvent))).scalars().all()) == 5
    # release hold, then apply deletes exactly the eligible rows
    await client.put(
        f"{P}/retention", headers=admin["h"], json={"retainDays": 30, "legalHold": False}
    )
    done = (
        await client.post(
            f"{P}/retention/apply", headers=admin["h"], json={"confirm": True, "expectedCount": 3}
        )
    ).json()
    assert done["deleted"] == 3
    async with factory() as db:
        assert len((await db.execute(select(AgentEvent))).scalars().all()) == 2


async def test_retention_project_override_team_hold_and_cross_tenant(env):
    client, factory = env
    a = await _member(factory)
    b = await _member(factory)
    pid = await _seed_events(factory, a["team"])
    # b cannot set a policy on a's project, nor see a's policies
    assert (
        await client.put(
            f"{P}/retention", headers=b["h"], json={"projectId": pid, "retainDays": 30}
        )
    ).status_code == 404
    await client.put(f"{P}/retention", headers=a["h"], json={"projectId": pid, "retainDays": 30})
    assert (await client.get(f"{P}/retention", headers=b["h"])).json()["items"] == []
    # a team-wide hold covers the project even though the project has its own period
    await client.put(
        f"{P}/retention",
        headers=a["h"],
        json={"retainDays": 365, "legalHold": True, "holdReason": "audit"},
    )
    plan = (await client.post(f"{P}/retention/plan", headers=a["h"])).json()
    assert plan["projects"][0]["held"] is True and plan["willDelete"] == 0
