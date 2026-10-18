import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from harness_extensions.catalog import CatalogRejected, sign_catalog, verify_catalog
from harness_extensions.config import Config
from harness_extensions.service import Principal, create_app
from harness_extensions.store import Store

TOOL = {"name": "search", "description": "find", "inputSchema": {"type": "object"}}
URL = "https://93.184.216.34/mcp"
KEY = Ed25519PrivateKey.generate()
PUB = KEY.public_key().public_bytes_raw().hex()


async def who(request: Request) -> Principal:
    # test stand-in for the S1 resolver: "<user>:<team>:<role>" bearer
    h = request.headers.get("authorization", "")
    if not h.startswith("Bearer "):
        raise HTTPException(401, "no token")
    u, t, r = h.removeprefix("Bearer ").split(":")
    return Principal(userId=u, teamId=t, role=r)


@pytest.fixture
def env(tmp_path):
    cfg = Config(identity_base=None, data_dir=tmp_path, catalog_public_key=PUB)
    store = Store(tmp_path / "e.db")
    return TestClient(create_app(cfg, store, who)), store, tmp_path


def H(team="t1", role="admin", user="u1"):
    return {"authorization": f"Bearer {user}:{team}:{role}"}


def reg(c, team="t1", **kw):
    body = {"kind": "mcp", "name": "srv", "url": URL, "tools": [TOOL]} | kw
    return c.post("/api/v1/extensions", json=body, headers=H(team))


def test_register_list_get_disable(env):
    c, _, _ = env
    r = reg(c)
    assert r.status_code == 201
    eid = r.json()["id"]
    assert [e["id"] for e in c.get("/api/v1/extensions", headers=H()).json()] == [eid]
    assert c.get(f"/api/v1/extensions/{eid}", headers=H()).json()["tools"][0]["digest"]
    assert c.post(f"/api/v1/extensions/{eid}/disable", headers=H()).json()["enabled"] is False


def test_skill_registers_without_url(env):
    c, _, _ = env
    assert reg(c, kind="skill", url=None, name="sk").status_code == 201


def test_unauthenticated_and_default_resolver_fail_closed(env, tmp_path):
    c, _, _ = env
    assert c.get("/api/v1/extensions").status_code == 401
    closed = TestClient(create_app(Config(identity_base=None, data_dir=tmp_path)))
    assert closed.get("/api/v1/extensions", headers=H()).status_code == 401


@pytest.mark.parametrize(
    "url", ["http://169.254.169.254/x", "https://127.0.0.1/x", "https://100.64.0.1/x", "ftp://a/b"]
)
def test_ssrf_urls_rejected(env, url):
    c, _, _ = env
    assert reg(c, url=url).status_code == 422
    assert c.get("/api/v1/extensions", headers=H()).json() == []


def test_hostile_tool_schema_rejected(env):
    c, _, _ = env
    bad = {"name": "x", "inputSchema": {"type": "object", "properties": {"a": {"$ref": "#"}}}}
    assert reg(c, tools=[bad]).status_code == 422


def test_cross_team_access_denied(env):
    c, _, _ = env
    eid = reg(c).json()["id"]
    other = H(team="t2")
    assert c.get("/api/v1/extensions", headers=other).json() == []
    for method, path in [
        ("get", f"/api/v1/extensions/{eid}"),
        ("post", f"/api/v1/extensions/{eid}/disable"),
        ("get", f"/api/v1/extensions/{eid}/grants"),
    ]:
        assert getattr(c, method)(path, headers=other).status_code == 404
    g = {"tool": "search", "subjectKind": "role", "subject": "admin"}
    assert c.post(f"/api/v1/extensions/{eid}/grants", json=g, headers=other).status_code == 404
    a = c.post(f"/api/v1/extensions/{eid}/authorize", json={"tool": "search"}, headers=other)
    assert a.status_code == 404
    # same name is free in another team
    assert reg(c, team="t2").status_code == 201


def test_team_in_body_is_ignored(env):
    c, _, _ = env
    r = c.post(
        "/api/v1/extensions",
        json={"kind": "skill", "name": "s", "teamId": "t2", "tools": []},
        headers=H("t1"),
    )
    assert r.status_code == 201
    assert c.get("/api/v1/extensions", headers=H("t2")).json() == []


def test_non_admin_cannot_mutate(env):
    c, _, _ = env
    eid = reg(c).json()["id"]
    v = H(role="viewer")
    assert reg(c, name="n2").status_code == 201
    assert (
        c.post("/api/v1/extensions", json={"kind": "skill", "name": "z"}, headers=v).status_code
        == 403
    )
    assert c.post(f"/api/v1/extensions/{eid}/disable", headers=v).status_code == 403
    assert c.get("/api/v1/extensions", headers=v).status_code == 200


def test_grants_default_deny_revoke_and_audit(env):
    c, store, _ = env
    eid = reg(c).json()["id"]
    call = lambda role="engineer", **b: c.post(  # noqa: E731
        f"/api/v1/extensions/{eid}/authorize", json={"tool": "search"} | b, headers=H(role=role)
    )
    assert call().status_code == 403  # ungranted: denied
    g = {"tool": "search", "subjectKind": "role", "subject": "engineer"}
    gid = c.post(f"/api/v1/extensions/{eid}/grants", json=g, headers=H()).json()["id"]
    assert call().status_code == 200
    assert call(role="viewer").status_code == 403  # role-scoped
    assert call(role="viewer", agent="a1").status_code == 403
    ag = {"tool": "search", "subjectKind": "agent", "subject": "a1"}
    c.post(f"/api/v1/extensions/{eid}/grants", json=ag, headers=H())
    assert call(role="viewer", agent="a1").status_code == 200
    assert c.delete(f"/api/v1/extensions/{eid}/grants/{gid}", headers=H()).status_code == 204
    assert call().status_code == 403  # revoked
    bad = {"tool": "nope", "subjectKind": "role", "subject": "x"}
    assert c.post(f"/api/v1/extensions/{eid}/grants", json=bad, headers=H()).status_code == 422
    actions = [r["action"] for r in store.audit_rows("t1")]
    assert {"extension.registered", "grant.added", "grant.revoked", "call.denied"} <= set(actions)


def test_disabled_extension_denies_even_with_grant(env):
    c, _, _ = env
    eid = reg(c).json()["id"]
    g = {"tool": "search", "subjectKind": "role", "subject": "engineer"}
    c.post(f"/api/v1/extensions/{eid}/grants", json=g, headers=H())
    c.post(f"/api/v1/extensions/{eid}/disable", headers=H())
    r = c.post(
        f"/api/v1/extensions/{eid}/authorize", json={"tool": "search"}, headers=H("t1", "engineer")
    )
    assert r.status_code == 403


ENTRIES = [{"kind": "mcp", "name": "vetted", "url": URL, "tools": [TOOL]}]


def test_catalog_verify_and_tamper():
    doc = sign_catalog(ENTRIES, KEY)
    assert verify_catalog(doc, PUB) == ENTRIES
    tampered = dict(doc, payload=doc["payload"].replace("vetted", "evil"))
    with pytest.raises(CatalogRejected):
        verify_catalog(tampered, PUB)
    with pytest.raises(CatalogRejected):
        verify_catalog(dict(doc, signature="00" * 64), PUB)
    with pytest.raises(CatalogRejected):  # signed by someone else
        verify_catalog(sign_catalog(ENTRIES, Ed25519PrivateKey.generate()), PUB)
    with pytest.raises(CatalogRejected):  # no trust anchor configured
        verify_catalog(doc, None)
    with pytest.raises(CatalogRejected):
        verify_catalog("junk", PUB)


def test_catalog_install_flow_and_tamper_rejected(env):
    c, store, tmp = env
    assert c.get("/api/v1/extensions/catalog", headers=H()).status_code == 503
    doc = sign_catalog(ENTRIES, KEY)
    (tmp / "catalog.json").write_text(json.dumps(doc))
    assert c.get("/api/v1/extensions/catalog", headers=H()).json() == ENTRIES
    r = c.post("/api/v1/extensions/catalog/install", json={"name": "vetted"}, headers=H())
    assert r.status_code == 201 and r.json()["name"] == "vetted"
    assert (
        c.post("/api/v1/extensions/catalog/install", json={"name": "zz"}, headers=H()).status_code
        == 404
    )
    v = c.post(
        "/api/v1/extensions/catalog/install", json={"name": "vetted"}, headers=H(role="viewer")
    )
    assert v.status_code == 403
    (tmp / "catalog.json").write_text(json.dumps(doc).replace("vetted", "evil!!"))
    for call in (
        lambda: c.get("/api/v1/extensions/catalog", headers=H()),
        lambda: c.post(
            "/api/v1/extensions/catalog/install", json={"name": "evil!!"}, headers=H("t2")
        ),
    ):
        assert call().status_code == 422
    assert c.get("/api/v1/extensions", headers=H("t2")).json() == []
    assert "catalog.installed" in [r["action"] for r in store.audit_rows("t1")]


def test_signed_catalog_entry_still_netguarded(env):
    c, _, tmp = env
    bad = [{"kind": "mcp", "name": "meta", "url": "http://169.254.169.254/", "tools": []}]
    (tmp / "catalog.json").write_text(json.dumps(sign_catalog(bad, KEY)))
    r = c.post("/api/v1/extensions/catalog/install", json={"name": "meta"}, headers=H())
    assert r.status_code == 422
