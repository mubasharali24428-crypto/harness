"""Workspace creation, naming, membership, project linking, visibility, search, quota."""

import pytest
from sqlalchemy import text

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
                               ("view@a.io", "viewer"), ("eng2@a.io", "engineer"))
        other = await seed_team(factory, ("admin@b.io", "admin"), name="other")
        yield client, factory, team, other


async def test_create_owner_audit_and_defaults(env):
    client, factory, team, _ = env
    ws = await create_ws(client, team["eng@a.io"], "Alpha One")
    assert ws["state"] == "active" and ws["visibility"] == "team"
    assert ws["ownerUserId"] == team["eng@a.io"]["userId"] and ws["settingsVersion"] == 1
    r = await client.get(f"{W}/{ws['id']}/members", headers=bearer(team["eng@a.io"]))
    assert r.json()["items"] == [{"userId": team["eng@a.io"]["userId"], "role": "owner"}]
    assert len(await audit_actions(factory, "workspace.created")) == 1


async def test_viewer_cannot_create_and_residency_required(env):
    client, _, team, _ = env
    r = await client.post(W, json={"name": "Nope", "dataResidency": "eu"},
                          headers=bearer(team["view@a.io"]))
    assert r.status_code == 403
    r = await client.post(W, json={"name": "NoRes"}, headers=bearer(team["eng@a.io"]))
    assert r.status_code == 422
    assert (await client.post(W, json={"name": "x", "dataResidency": "eu"})).status_code == 401


@pytest.mark.parametrize("name", ["", " ", "a", "-bad", "bad/name", "x" * 81, "semi;colon"])
async def test_name_validation(env, name):
    client, _, team, _ = env
    r = await client.post(W, json={"name": name, "dataResidency": "eu"},
                          headers=bearer(team["eng@a.io"]))
    assert r.status_code == 422, r.text


async def test_name_unique_per_team_case_insensitive_not_across_teams(env):
    client, _, team, other = env
    await create_ws(client, team["eng@a.io"], "Dup")
    r = await client.post(W, json={"name": "dup", "dataResidency": "eu"},
                          headers=bearer(team["eng2@a.io"]))
    assert r.status_code == 409 and r.json()["detail"]["error"]["code"] == "NAME_TAKEN"
    await create_ws(client, other["admin@b.io"], "Dup")  # other team may reuse the name


async def test_visibility_and_residency_validation(env):
    client, _, team, _ = env
    for body in ({"visibility": "public"}, {"dataResidency": "mars"}, {"visibility": "org"}):
        r = await client.post(W, json={"name": "V1", "dataResidency": "eu", **body},
                              headers=bearer(team["eng@a.io"]))
        assert r.status_code == 422, (body, r.text)  # org needs an org-linked team
    ws = await create_ws(client, team["eng@a.io"], "V2", visibility="private", dataResidency="EU")
    assert ws["visibility"] == "private" and ws["dataResidency"] == "eu"
    r = await client.patch(f"{W}/{ws['id']}", json={"dataResidency": "xx"},
                           headers=bearer(team["eng@a.io"]))
    assert r.status_code == 422


async def test_private_workspace_hidden_from_nonmembers_but_not_admin(env):
    client, factory, team, other = env
    ws = await create_ws(client, team["eng@a.io"], "Secret", visibility="private")
    url = f"{W}/{ws['id']}"
    assert (await client.get(url, headers=bearer(team["eng2@a.io"]))).status_code == 404
    assert (await client.get(url, headers=bearer(other["admin@b.io"]))).status_code == 404
    assert (await client.get(url, headers=bearer(team["admin@a.io"]))).status_code == 200
    r = await client.put(f"{url}/members/{team['eng2@a.io']['userId']}", json={"role": "member"},
                         headers=bearer(team["eng@a.io"]))
    assert r.status_code == 200
    assert (await client.get(url, headers=bearer(team["eng2@a.io"]))).status_code == 200


async def test_team_visibility_readable_by_team_not_writable_by_nonowner(env):
    client, _, team, other = env
    ws = await create_ws(client, team["eng@a.io"], "Shared")
    url = f"{W}/{ws['id']}"
    assert (await client.get(url, headers=bearer(team["view@a.io"]))).status_code == 200
    r = await client.patch(url, json={"tags": ["x"]}, headers=bearer(team["eng2@a.io"]))
    assert r.status_code == 403
    assert (await client.patch(url, json={"tags": ["x"]},
                               headers=bearer(other["admin@b.io"]))).status_code == 404


async def test_members_must_be_team_members_and_last_owner_protected(env):
    client, _, team, other = env
    ws = await create_ws(client, team["eng@a.io"], "Mem")
    url = f"{W}/{ws['id']}/members"
    r = await client.put(f"{url}/{other['admin@b.io']['userId']}", json={"role": "member"},
                         headers=bearer(team["eng@a.io"]))
    assert r.status_code == 422 and r.json()["detail"]["error"]["code"] == "NOT_A_TEAM_MEMBER"
    r = await client.put(f"{url}/{team['eng@a.io']['userId']}", json={"role": "viewer"},
                         headers=bearer(team["eng@a.io"]))
    assert r.status_code == 409 and r.json()["detail"]["error"]["code"] == "LAST_OWNER"
    r = await client.delete(f"{url}/{team['eng@a.io']['userId']}", headers=bearer(team["eng@a.io"]))
    assert r.status_code == 409
    r = await client.put(f"{url}/{team['eng2@a.io']['userId']}", json={"role": "boss"},
                         headers=bearer(team["eng@a.io"]))
    assert r.status_code == 422


async def test_project_link_grants_no_workspace_membership(env):
    client, factory, team, other = env
    ws = await create_ws(client, team["eng@a.io"], "Linked", visibility="private")
    pid = await seed_project(factory, team["teamId"])
    url = f"{W}/{ws['id']}"
    r = await client.post(f"{url}/projects", json={"projectId": pid},
                          headers=bearer(team["eng@a.io"]))
    assert r.status_code == 201, r.text
    # collaboration access unchanged: still exactly one member (the owner)
    async with factory() as db:
        n = (await db.execute(text("SELECT COUNT(*) FROM workspace_members"))).scalar()
        assert n == 1
        linked = (await db.execute(text("SELECT workspace_id FROM projects WHERE id=:p"),
                                   {"p": pid})).scalar()
        assert linked == ws["id"]
    # a teammate who can use the repo/project still cannot see the private workspace
    assert (await client.get(url, headers=bearer(team["eng2@a.io"]))).status_code == 404
    assert (await client.get(f"{url}/projects", headers=bearer(team["eng2@a.io"]))).status_code == 404  # noqa: E501
    # and workspace membership grants nothing on the project row
    await client.put(f"{url}/members/{team['eng2@a.io']['userId']}", json={"role": "viewer"},
                     headers=bearer(team["eng@a.io"]))
    async with factory() as db:
        repo = (await db.execute(text("SELECT repo_full_name FROM projects WHERE id=:p"),
                                 {"p": pid})).scalar()
        assert repo == "acme/repo"  # repository binding is untouched by membership
    # foreign project 404; double link to another workspace 409; same workspace idempotent
    foreign = await seed_project(factory, other["teamId"], "f")
    assert (await client.post(f"{url}/projects", json={"projectId": foreign},
                              headers=bearer(team["eng@a.io"]))).status_code == 404
    ws2 = await create_ws(client, team["eng@a.io"], "Linked2")
    r = await client.post(f"{W}/{ws2['id']}/projects", json={"projectId": pid},
                          headers=bearer(team["eng@a.io"]))
    assert r.status_code == 409
    assert (await client.post(f"{url}/projects", json={"projectId": pid},
                              headers=bearer(team["eng@a.io"]))).status_code == 201
    r = await client.delete(f"{url}/projects/{pid}", headers=bearer(team["eng@a.io"]))
    assert r.status_code == 204


async def test_search_filters_scoping_and_paging(env):
    client, _, team, other = env
    eng, adm = team["eng@a.io"], team["admin@a.io"]
    await client.post(f"{W}/templates", headers=bearer(adm),
                      json={"name": "std", "defaults": {"tags": ["blue"]}})
    for i in range(5):
        await create_ws(client, eng, f"Svc {i}", tags=["backend"] if i % 2 else [])
    await create_ws(client, eng, "Web_100", templateName="std")
    await create_ws(client, eng, "Hidden", visibility="private")
    await create_ws(client, other["admin@b.io"], "Svc foreign")

    def ids(r):
        return [i["name"] for i in r.json()["items"]]

    r = await client.get(W, params={"q": "svc"}, headers=bearer(team["eng2@a.io"]))
    assert ids(r) == [f"Svc {i}" for i in range(5)]  # no foreign, ordered
    assert "Hidden" not in ids(await client.get(W, headers=bearer(team["eng2@a.io"])))
    assert "Hidden" in ids(await client.get(W, headers=bearer(adm)))
    assert ids(await client.get(W, params={"tag": "backend"}, headers=bearer(eng))) == [
        "Svc 1", "Svc 3"]
    assert ids(await client.get(W, params={"template": "std"}, headers=bearer(eng))) == [
        "Web_100"]
    assert ids(await client.get(W, params={"q": "_100"}, headers=bearer(eng))) == ["Web_100"]
    assert ids(await client.get(W, params={"q": "_"}, headers=bearer(eng))) == ["Web_100"]
    assert ids(await client.get(W, params={"q": "%"}, headers=bearer(eng))) == []
    assert (await client.get(W, params={"state": "bogus"}, headers=bearer(eng))).status_code == 422
    page, seen, cur = None, [], None
    while True:
        params = {"limit": 2, **({"cursor": cur} if cur else {})}
        page = (await client.get(W, params=params, headers=bearer(eng))).json()
        assert len(page["items"]) <= 2
        seen += [i["name"] for i in page["items"]]
        cur = page["nextCursor"]
        if not cur:
            break
    assert len(seen) == len(set(seen)) == 7 and "Svc foreign" not in seen
    r = await client.get(W, params={"limit": 100000}, headers=bearer(eng))
    assert r.status_code == 200
    # a foreign cursor cannot be used to probe other teams
    foreign_id = (await client.get(W, headers=bearer(other["admin@b.io"]))).json()["items"][0]["id"]
    r = await client.get(W, params={"cursor": foreign_id}, headers=bearer(eng))
    assert r.json()["items"] == []


async def test_quota_enforced_and_deleted_frees_slot():
    async with make_env(max_per_team=2) as (client, factory):
        team = await seed_team(factory, ("admin@q.io", "admin"))
        adm = team["admin@q.io"]
        a = await create_ws(client, adm, "Q1")
        await create_ws(client, adm, "Q2")
        r = await client.post(W, json={"name": "Q3", "dataResidency": "eu"}, headers=bearer(adm))
        assert r.status_code == 409 and r.json()["detail"]["error"]["code"] == "QUOTA_EXCEEDED"
        await client.post(f"{W}/{a['id']}/archive", headers=bearer(adm))
        await client.post(f"{W}/{a['id']}/delete", json={"reason": "cleanup"}, headers=bearer(adm))
        await create_ws(client, adm, "Q3")  # deleted workspaces do not count
        other = await seed_team(factory, ("admin@q2.io", "admin"), name="q2")
        await create_ws(client, other["admin@q2.io"], "Q1")  # quota is per team
