"""Workspace settings versions, templates, bindings, org visibility, lifecycle transitions."""

import pytest
from sqlalchemy import text

from harness_identity.workspaces import RETENTION_DAYS, TRANSITIONS
from tests.test_workspaces_support import (
    audit_actions,
    bearer,
    create_ws,
    make_env,
    seed_project,
    seed_team,
)

W = "/api/v1/workspaces"


@pytest.fixture
async def env():
    async with make_env() as (client, factory):
        team = await seed_team(factory, ("admin@a.io", "admin"), ("eng@a.io", "engineer"),
                               ("eng2@a.io", "engineer"))
        yield client, factory, team


def err(r):
    return r.json()["detail"]["error"]["code"]


# ------------------------------------------------------------------ settings


async def test_settings_versions_before_after_audit_and_revert(env):
    client, factory, team = env
    eng = team["eng@a.io"]
    ws = await create_ws(client, eng, "Cfg")
    url = f"{W}/{ws['id']}/settings"
    r = await client.put(url, json={"settings": {"default_branch": "main", "max_concurrent_runs": 3}},  # noqa: E501
                         headers=bearer(eng))
    assert r.status_code == 200 and r.json()["settingsVersion"] == 2
    r = await client.put(url, json={"settings": {"max_concurrent_runs": 5, "default_branch": None}},
                         headers=bearer(eng))
    assert r.json()["settings"] == {"max_concurrent_runs": 5} and r.json()["settingsVersion"] == 3
    hist = (await client.get(f"{url}/history", headers=bearer(eng))).json()["items"]
    assert [h["version"] for h in hist] == [3, 2, 1]
    assert hist[0]["before"] == {"default_branch": "main", "max_concurrent_runs": 3}
    assert hist[0]["after"] == {"max_concurrent_runs": 5} and hist[0]["changedBy"] == eng["userId"]
    # revert creates a NEW version (history is never rewritten)
    r = await client.post(f"{url}/revert", json={"toVersion": 2}, headers=bearer(eng))
    assert r.status_code == 200 and r.json()["settingsVersion"] == 4
    assert r.json()["settings"] == {"default_branch": "main", "max_concurrent_runs": 3}
    hist = (await client.get(f"{url}/history", headers=bearer(eng))).json()["items"]
    assert hist[0]["revertedFrom"] == 2 and len(hist) == 4
    assert (await client.post(f"{url}/revert", json={"toVersion": 99},
                              headers=bearer(eng))).status_code == 404
    rows = await audit_actions(factory, "workspace.settings_changed")
    assert len(rows) == 3
    assert rows[-1].detail["before"] == {"max_concurrent_runs": 5}
    assert rows[-1].detail["revertedFrom"] == 2


async def test_settings_noop_conflict_validation_and_authz(env):
    client, factory, team = env
    eng = team["eng@a.io"]
    ws = await create_ws(client, eng, "Cfg2")
    url = f"{W}/{ws['id']}/settings"
    await client.put(url, json={"settings": {"max_concurrent_runs": 2}}, headers=bearer(eng))
    r = await client.put(url, json={"settings": {"max_concurrent_runs": 2}}, headers=bearer(eng))
    assert r.json()["settingsVersion"] == 2  # identical change: no new version
    assert len(await audit_actions(factory, "workspace.settings_changed")) == 1
    r = await client.put(url, json={"settings": {"max_concurrent_runs": 4}, "expectedVersion": 1},
                         headers=bearer(eng))
    assert r.status_code == 409 and err(r) == "VERSION_CONFLICT"
    for bad in ({"nope": 1}, {"max_concurrent_runs": 0}, {"max_concurrent_runs": True},
                {"default_branch": "bad branch"}, {"require_plan_approval": "yes"}):
        r = await client.put(url, json={"settings": bad}, headers=bearer(eng))
        assert r.status_code == 422, bad
    r = await client.put(url, json={"settings": {"description": "x"}}, headers=bearer(team["eng2@a.io"]))  # noqa: E501
    assert r.status_code == 403
    assert (await client.get(url, headers=bearer(team["eng2@a.io"]))).status_code == 200


# ------------------------------------------------------------------ templates


async def test_template_versions_apply_records_version(env):
    client, factory, team = env
    adm, eng = team["admin@a.io"], team["eng@a.io"]
    body = {"name": "secure", "defaults": {
        "visibility": "private", "dataResidency": "uk", "tags": ["secure"],
        "settings": {"require_plan_approval": True}}}
    assert (await client.post(f"{W}/templates", json=body, headers=bearer(eng))).status_code == 403
    r1 = await client.post(f"{W}/templates", json=body, headers=bearer(adm))
    assert r1.status_code == 201 and r1.json()["version"] == 1
    body["defaults"]["settings"] = {"require_plan_approval": True, "max_concurrent_runs": 2}
    r2 = await client.post(f"{W}/templates", json=body, headers=bearer(adm))
    assert r2.json()["version"] == 2
    bad = await client.post(f"{W}/templates", json={"name": "bad", "defaults": {"x": 1}},
                            headers=bearer(adm))
    assert bad.status_code == 422
    bad = await client.post(f"{W}/templates", json={"name": "bad", "defaults": {"dataResidency": "zz"}},  # noqa: E501
                            headers=bearer(adm))
    assert bad.status_code == 422
    ws = await create_ws(client, eng, "Plain")
    r = await client.post(f"{W}/{ws['id']}/apply-template", json={"name": "secure", "version": 1},
                          headers=bearer(eng))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["template"] == {"name": "secure", "version": 1} and out["visibility"] == "private"
    assert out["dataResidency"] == "uk" and out["tags"] == ["secure"]
    assert out["settingsVersion"] == 2
    hist = (await client.get(f"{W}/{ws['id']}/settings/history", headers=bearer(eng))).json()
    assert hist["items"][0]["template"] == {"name": "secure", "version": 1}
    applied = await audit_actions(factory, "workspace.template_applied")
    assert applied[-1].detail["templateVersion"] == 1
    assert len(await audit_actions(factory, "workspace.template_saved")) == 2
    # create from latest template version
    ws2 = await create_ws(client, eng, "FromTpl", templateName="secure", dataResidency="eu")
    assert ws2["template"] == {"name": "secure", "version": 2} and ws2["dataResidency"] == "eu"
    assert ws2["settings"]["max_concurrent_runs"] == 2 and ws2["visibility"] == "private"
    missing = await client.post(f"{W}/{ws['id']}/apply-template", json={"name": "ghost"},
                                headers=bearer(eng))
    assert missing.status_code == 404


# ------------------------------------------------------------------ bindings


async def test_bindings_require_allowlist(env):
    client, factory, team = env
    adm, eng = team["admin@a.io"], team["eng@a.io"]
    ws = await create_ws(client, eng, "Bind")
    url = f"{W}/{ws['id']}/bindings"
    repo = {"kind": "repository", "value": "acme/app"}
    r = await client.post(url, json=repo, headers=bearer(eng))
    assert r.status_code == 403 and err(r) == "BINDING_NOT_APPROVED"  # deny by default
    assert (await client.post(f"{W}/allowlist", json=repo, headers=bearer(eng))).status_code == 403
    a = await client.post(f"{W}/allowlist", json=repo, headers=bearer(adm))
    assert a.status_code == 201
    assert (await client.post(url, json=repo, headers=bearer(eng))).status_code == 201
    # kind is part of the match: same value as an environment is NOT approved
    r = await client.post(url, json={"kind": "environment", "value": "acme/app"}, headers=bearer(eng))  # noqa: E501
    assert r.status_code == 403
    assert (await client.post(url, json={"kind": "other", "value": "x"},
                              headers=bearer(eng))).status_code == 422
    # another team's allowlist does not approve for this team
    other = await seed_team(factory, ("admin@z.io", "admin"), name="z")
    wz = await create_ws(client, other["admin@z.io"], "Bind")
    r = await client.post(f"{W}/{wz['id']}/bindings", json=repo, headers=bearer(other["admin@z.io"]))  # noqa: E501
    assert r.status_code == 403
    # revoking approval is visible on the existing binding
    await client.delete(f"{W}/allowlist/{a.json()['id']}", headers=bearer(adm))
    items = (await client.get(url, headers=bearer(eng))).json()["items"]
    assert items[0]["approved"] is False
    assert (await client.post(url, json={"kind": "repository", "value": "acme/new"},
                              headers=bearer(eng))).status_code == 403


async def test_org_allowlist_and_org_visibility(env):
    client, factory, team = env
    async with factory() as db:  # lane O adds teams.org_id; emulate it for the test
        await db.execute(text("ALTER TABLE teams ADD COLUMN org_id VARCHAR(36)"))
        await db.commit()
    sib = await seed_team(factory, ("admin@s.io", "admin"), ("eng@s.io", "engineer"), name="sib")
    async with factory() as db:
        await db.execute(text("UPDATE teams SET org_id='org-1' WHERE id IN (:a, :b)"),
                         {"a": team["teamId"], "b": sib["teamId"]})
        await db.commit()
    eng = team["eng@a.io"]
    ws = await create_ws(client, eng, "OrgWide", visibility="org")
    assert (await client.get(f"{W}/{ws['id']}", headers=bearer(sib["eng@s.io"]))).status_code == 200
    r = await client.patch(f"{W}/{ws['id']}", json={"tags": ["x"]}, headers=bearer(sib["admin@s.io"]))  # noqa: E501
    assert r.status_code == 404  # org visibility is read-only across teams
    # a team outside the org never sees it
    out = await seed_team(factory, ("admin@o.io", "admin"), name="outside")
    assert (await client.get(f"{W}/{ws['id']}", headers=bearer(out["admin@o.io"]))).status_code == 404  # noqa: E501
    # sibling team in the same org inherits the org allowlist entry
    await client.post(f"{W}/allowlist", json={"kind": "environment", "value": "prod-eu"},
                      headers=bearer(team["admin@a.io"]))
    ws_s = await create_ws(client, sib["eng@s.io"], "SibWs")
    r = await client.post(f"{W}/{ws_s['id']}/bindings",
                          json={"kind": "environment", "value": "prod-eu"}, headers=bearer(sib["eng@s.io"]))  # noqa: E501
    assert r.status_code == 201


# ------------------------------------------------------------------ lifecycle


async def _post(client, actor, ws_id, action, **body):
    return await client.post(f"{W}/{ws_id}/{action}", json=body or None, headers=bearer(actor))


async def test_transition_table_shape():
    assert TRANSITIONS["delete"][0] == frozenset({"archived"})
    assert {t for _, t in TRANSITIONS.values()} == {"paused", "archived", "active", "deleted"}


async def test_full_matrix_of_transitions(env):
    client, factory, team = env
    eng = team["eng@a.io"]
    # (action, state it is attempted from) -> expected status
    legal = {("pause", "active"), ("archive", "active"), ("archive", "paused"),
             ("restore", "paused"), ("restore", "archived")}
    actions = ["pause", "archive", "restore"]
    reach = {"active": [], "paused": ["pause"], "archived": ["archive"]}
    n = 0
    for state, path in reach.items():
        for action in actions:
            n += 1
            ws = await create_ws(client, eng, f"M{n}")
            for step in path:
                assert (await _post(client, eng, ws["id"], step)).status_code == 200
            r = await _post(client, eng, ws["id"], action)
            target = TRANSITIONS[action][1]
            if state == target:
                assert r.status_code == 200 and r.json()["changed"] is False  # idempotent
            elif (action, state) in legal:
                assert r.status_code == 200 and r.json()["state"] == target
            else:
                assert r.status_code == 409 and err(r) == "ILLEGAL_TRANSITION", (action, state)


async def test_idempotent_no_duplicate_audit_and_reason_recorded(env):
    client, factory, team = env
    eng = team["eng@a.io"]
    ws = await create_ws(client, eng, "Idem")
    r1 = await _post(client, eng, ws["id"], "pause", reason="freeze")
    r2 = await _post(client, eng, ws["id"], "pause")
    assert r1.json()["changed"] is True and r2.json()["changed"] is False
    assert r2.json()["stateReason"] == "freeze"
    rows = await audit_actions(factory, "workspace.paused")
    assert len(rows) == 1 and rows[0].detail["before"] == {"state": "active"}
    assert rows[0].detail["after"] == {"state": "paused"} and rows[0].detail["reason"] == "freeze"
    await _post(client, eng, ws["id"], "restore")
    await _post(client, eng, ws["id"], "archive")
    await _post(client, eng, ws["id"], "archive")
    assert len(await audit_actions(factory, "workspace.restored")) == 1
    assert len(await audit_actions(factory, "workspace.archived")) == 1


async def test_delete_rules_and_soft_delete(env):
    client, factory, team = env
    eng, adm = team["eng@a.io"], team["admin@a.io"]
    ws = await create_ws(client, eng, "Del")
    wid = ws["id"]
    r = await _post(client, adm, wid, "delete", reason="no longer needed")
    assert r.status_code == 409 and err(r) == "WORKSPACE_NOT_ARCHIVED"  # active
    await _post(client, eng, wid, "pause")
    assert err(await _post(client, adm, wid, "delete", reason="no longer needed")) == \
        "WORKSPACE_NOT_ARCHIVED"
    await _post(client, eng, wid, "archive")
    assert (await _post(client, eng, wid, "delete", reason="no longer needed")).status_code == 403
    assert (await _post(client, adm, wid, "delete", reason="")).status_code == 422
    assert (await _post(client, adm, wid, "delete", reason="  x ")).status_code == 422
    r = await _post(client, adm, wid, "delete", reason="no longer needed")
    assert r.status_code == 200 and r.json()["state"] == "deleted" and r.json()["purgeAfter"]
    again = await _post(client, adm, wid, "delete", reason="no longer needed")
    assert again.status_code == 200 and again.json()["changed"] is False
    # terminal: nothing leaves deleted; row is retained (soft delete)
    for action in ("pause", "archive", "restore"):
        assert (await _post(client, adm, wid, action)).status_code == 409
    async with factory() as db:
        row = (await db.execute(text("SELECT state, deleted_at, purge_after FROM workspaces"
                                     " WHERE id=:i"), {"i": wid})).first()
    assert row.state == "deleted" and row.deleted_at and row.purge_after
    assert RETENTION_DAYS == 30
    # only admins can still see it; the name is free again
    assert (await client.get(f"{W}/{wid}", headers=bearer(eng))).status_code == 404
    assert (await client.get(f"{W}/{wid}", headers=bearer(adm))).status_code == 200
    await create_ws(client, eng, "Del")
    deleted = await audit_actions(factory, "workspace.deleted")
    assert len(deleted) == 1 and deleted[0].detail["reason"] == "no longer needed"


async def test_archived_workspace_is_read_only_but_unlink_allowed(env):
    client, factory, team = env
    eng = team["eng@a.io"]
    ws = await create_ws(client, eng, "Frozen")
    pid = await seed_project(factory, team["teamId"])
    await client.post(f"{W}/{ws['id']}/projects", json={"projectId": pid}, headers=bearer(eng))
    await _post(client, eng, ws["id"], "archive")
    r = await client.put(f"{W}/{ws['id']}/settings", json={"settings": {"description": "x"}},
                         headers=bearer(eng))
    assert r.status_code == 409 and err(r) == "ILLEGAL_STATE"
    assert (await client.patch(f"{W}/{ws['id']}", json={"tags": ["a"]},
                               headers=bearer(eng))).status_code == 409
    assert (await client.delete(f"{W}/{ws['id']}/projects/{pid}",
                                headers=bearer(eng))).status_code == 204
    assert (await _post(client, eng, ws["id"], "restore")).json()["state"] == "active"


async def test_viewer_role_cannot_transition_and_foreign_is_404(env):
    client, factory, team = env
    ws = await create_ws(client, team["eng@a.io"], "Authz")
    assert (await _post(client, team["eng2@a.io"], ws["id"], "pause")).status_code == 403
    other = await seed_team(factory, ("admin@x.io", "admin"), name="x")
    assert (await _post(client, other["admin@x.io"], ws["id"], "pause")).status_code == 404
    assert (await _post(client, team["admin@a.io"], ws["id"], "pause")).status_code == 200
