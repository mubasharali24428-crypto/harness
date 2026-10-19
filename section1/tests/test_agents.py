"""Part 45 (lane A): agent registry - versions, approval workflow, selection, isolation."""

import pytest

from tests.test_agents_support import A, audit_actions, bearer, body, make_env, register, seed_team


@pytest.fixture
async def env():
    async with make_env() as (client, factory):
        team = await seed_team(factory, ("a1@a.io", "admin"), ("a2@a.io", "admin"),
                               ("eng@a.io", "engineer"), ("view@a.io", "viewer"))
        other = await seed_team(factory, ("admin@b.io", "admin"), name="other")
        yield client, factory, team, other


def code(r) -> str:
    return r.json()["detail"]["error"]["code"]


async def test_register_creates_proposed_v1_and_audits(env):
    client, factory, team, _ = env
    a = await register(client, team["a1@a.io"])
    assert a["state"] == "proposed" and a["version"] == 1 and a["name"] == "Coder"
    assert a["toolPermissions"] == ["read_file", "search", "write_file"]
    assert a["maxBudgetUsd"] == "2.5" and a["createdBy"] == team["a1@a.io"]["userId"]
    assert len(await audit_actions(factory, "agent.registered")) == 1


async def test_only_admins_write_everyone_in_team_reads(env):
    client, _, team, other = env
    for who in ("eng@a.io", "view@a.io"):
        r = await client.post(A, json=body(), headers=bearer(team[who]))
        assert r.status_code == 403
    assert (await client.post(A, json=body())).status_code == 401
    a = await register(client, team["a1@a.io"])
    seen = await client.get(f"{A}/{a['id']}", headers=bearer(team["view@a.io"]))
    assert seen.status_code == 200
    r = await client.get(f"{A}/{a['id']}", headers=bearer(other["admin@b.io"]))
    assert r.status_code == 404 and code(r) == "NOT_FOUND"  # same as unknown id
    assert (await client.get(f"{A}/nope", headers=bearer(other["admin@b.io"]))).status_code == 404
    assert (await client.get(A, headers=bearer(other["admin@b.io"]))).json()["items"] == []
    for verb in ("approve", "retire"):
        r = await client.post(f"{A}/{a['id']}/{verb}", headers=bearer(other["admin@b.io"]))
        assert r.status_code == 404


@pytest.mark.parametrize("over", [
    {"name": "x"}, {"name": "-bad"}, {"permissions": ["approve"]}, {"permissions": []},
    {"permissions": ["administer"]}, {"capabilities": ["Bad Cap"]},
    {"toolPermissions": ["bad tool!"]}, {"allowedPaths": ["../x"]}, {"allowedPaths": ["/abs"]},
    {"networkAllow": ["http://x"]}, {"credentialRefs": ["1bad"]}, {"model": "bad model"},
    {"fallbackModel": "deepseek-chat"}, {"toolFallbacks": {"search": "unknown"}},
    {"toolFallbacks": {"search": "search"}}, {"maxDepth": 9}, {"maxChildren": 99},
    {"maxBudgetUsd": "5000"}, {"maxBudgetUsd": "-1"}, {"reasoning": "hidden"},
])
async def test_definition_validation(env, over):
    client, _, team, _ = env
    r = await client.post(A, json=body(**over), headers=bearer(team["a1@a.io"]))
    assert r.status_code == 422, r.text


async def test_name_unique_per_team_case_insensitive(env):
    client, _, team, other = env
    await register(client, team["a1@a.io"])
    r = await client.post(A, json=body(name="coder"), headers=bearer(team["a2@a.io"]))
    assert r.status_code == 409 and code(r) == "NAME_TAKEN"
    await register(client, other["admin@b.io"])  # another team may reuse the name


async def test_put_saves_a_new_version_and_keeps_history(env):
    client, factory, team, _ = env
    v1 = await register(client, team["a1@a.io"])
    r = await client.put(f"{A}/{v1['id']}", json=body(model="deepseek-pro", name=None),
                         headers=bearer(team["a1@a.io"]))
    assert r.status_code == 200
    v2 = r.json()
    assert v2["version"] == 2 and v2["state"] == "proposed" and v2["id"] != v1["id"]
    again = (await client.get(f"{A}/{v1['id']}", headers=bearer(team["a1@a.io"]))).json()
    assert again["model"] == "deepseek-chat" and again["version"] == 1  # untouched
    r = await client.put(f"{A}/{v1['id']}", json=body(name="Other"),
                         headers=bearer(team["a1@a.io"]))
    assert r.status_code == 422  # a family keeps its name
    items = (await client.get(f"{A}?name=coder", headers=bearer(team["eng@a.io"]))).json()["items"]
    assert [i["version"] for i in items] == [1, 2]
    rows = await audit_actions(factory, "agent.updated")
    assert len(rows) == 1 and rows[0].detail["before"]["model"] == "deepseek-chat"
    assert rows[0].detail["after"]["model"] == "deepseek-pro"


async def test_approval_workflow_two_person_rule_and_idempotence(env, monkeypatch):
    monkeypatch.delenv("HARNESS_AGENT_ALLOW_SELF_APPROVAL", raising=False)
    client, factory, team, _ = env
    a = await register(client, team["a1@a.io"])
    url = f"{A}/{a['id']}/approve"
    r = await client.post(url, headers=bearer(team["a1@a.io"]))
    assert r.status_code == 403 and code(r) == "SELF_APPROVAL"
    assert (await client.post(url, headers=bearer(team["eng@a.io"]))).status_code == 403
    r = await client.post(url, headers=bearer(team["a2@a.io"]))
    assert r.status_code == 200 and r.json()["state"] == "approved" and r.json()["changed"]
    assert r.json()["approvedBy"] == team["a2@a.io"]["userId"]
    assert (await client.post(url, headers=bearer(team["a2@a.io"]))).json()["changed"] is False
    assert len(await audit_actions(factory, "agent.approved")) == 1
    monkeypatch.setenv("HARNESS_AGENT_ALLOW_SELF_APPROVAL", "1")
    b = await register(client, team["a1@a.io"], name="Solo")
    assert (await client.post(f"{A}/{b['id']}/approve", headers=bearer(team["a1@a.io"]))
            ).status_code == 200


async def test_retire_is_terminal_and_audited_with_before_after(env):
    client, factory, team, _ = env
    a = await register(client, team["a1@a.io"])
    await client.post(f"{A}/{a['id']}/approve", headers=bearer(team["a2@a.io"]))
    r = await client.post(f"{A}/{a['id']}/retire", json={"reason": "obsolete"},
                          headers=bearer(team["a1@a.io"]))
    assert r.status_code == 200 and r.json()["state"] == "retired"
    assert r.json()["retiredReason"] == "obsolete"
    row = (await audit_actions(factory, "agent.retired"))[0]
    assert row.detail["before"] == {"state": "approved"}
    assert row.detail["after"] == {"state": "retired"}
    again = await client.post(f"{A}/{a['id']}/retire", headers=bearer(team["a1@a.io"]))
    assert again.json()["changed"] is False
    r = await client.post(f"{A}/{a['id']}/approve", headers=bearer(team["a2@a.io"]))
    assert r.status_code == 409 and code(r) == "ILLEGAL_TRANSITION"
    r = await client.put(f"{A}/{a['id']}", json=body(), headers=bearer(team["a1@a.io"]))
    assert r.status_code == 409 and code(r) == "ILLEGAL_STATE"


async def test_selection_returns_only_latest_approved_matching_agents(env):
    client, factory, team, _ = env
    admin, second, eng = team["a1@a.io"], team["a2@a.io"], team["eng@a.io"]

    async def approved(**over):
        a = await register(client, admin, **over)
        await client.post(f"{A}/{a['id']}/approve", headers=bearer(second))
        return a

    coder = await approved()
    await register(client, admin, name="Draft")  # proposed: never selectable
    retired = await approved(name="Gone")
    await client.post(f"{A}/{retired['id']}/retire", headers=bearer(admin))
    v2 = (await client.put(f"{A}/{coder['id']}", json=body(name=None),
                           headers=bearer(admin))).json()
    sel = f"{A}/select"
    r = await client.post(sel, json={"capabilities": ["code.python"], "tools": ["read_file"]},
                          headers=bearer(eng))
    assert [i["id"] for i in r.json()["items"]] == [coder["id"]]  # v2 is still proposed
    await client.post(f"{A}/{v2['id']}/approve", headers=bearer(second))
    r = await client.post(sel, json={"capabilities": ["code.python"]}, headers=bearer(eng))
    assert [i["id"] for i in r.json()["items"]] == [v2["id"]]  # newest approved version only
    for none in ({"capabilities": ["code.rust"]}, {"tools": ["rm_rf"]}, {"name": "Draft"},
                 {"name": "Gone"}):
        assert (await client.post(sel, json=none, headers=bearer(eng))).json()["items"] == []
    assert (await client.post(sel, json={}, headers=bearer(team["view@a.io"]))).status_code == 403
    # the refused viewer call leaves no audit row
    assert len(await audit_actions(factory, "agent.selected")) == 6


async def test_selection_never_crosses_teams(env):
    client, _, team, other = env
    a = await register(client, team["a1@a.io"])
    await client.post(f"{A}/{a['id']}/approve", headers=bearer(team["a2@a.io"]))
    r = await client.post(f"{A}/select", json={}, headers=bearer(other["admin@b.io"]))
    assert r.json()["items"] == []
