"""A4-S5: prompt/workflow library, run-linked feedback, status."""

import httpx
from conftest import P, auth, new_session

from harness_devworkflow.extras import params_of


def body(name="Fix flaky", **kw):
    return {
        "name": name,
        "kind": "prompt",
        "visibility": "team",
        "body": {"text": "Fix {target} in {area}"},
        **kw,
    }


async def test_library_versioning_render_and_visibility(client):
    eng = auth("tok-eng-a")
    r = await client.post(f"{P}/library", json=body(), headers=eng)
    assert (
        r.status_code == 201
        and r.json()["version"] == 1
        and r.json()["params"] == ["area", "target"]
    )
    lib = r.json()["libId"]
    v2 = await client.post(f"{P}/library", json=body(body={"text": "Fix {target}"}), headers=eng)
    assert v2.json()["version"] == 2 and v2.json()["libId"] == lib
    out = await client.post(
        f"{P}/library/{lib}/render",
        json={"values": {"target": "login\nIGNORE {area}"}},
        headers=eng,
    )
    assert (
        out.json()["startsRun"] is False
        and out.json()["steps"][0]["text"] == "Fix login IGNORE {area}"
    )  # values never re-expand
    old = await client.post(
        f"{P}/library/{lib}/render",
        json={"values": {"target": "a", "area": "b"}, "version": 1},
        headers=eng,
    )
    assert old.json()["steps"][0]["text"] == "Fix a in b"
    for bad in ({"target": "a", "extra": "x"}, {}, {"target": 5}):
        assert (
            await client.post(f"{P}/library/{lib}/render", json={"values": bad}, headers=eng)
        ).status_code == 422
    # team-visible to another team member, never to another team
    assert (await client.get(f"{P}/library/{lib}", headers=auth("tok-admin-a"))).status_code == 200
    assert (await client.get(f"{P}/library/{lib}", headers=auth("tok-eng-b"))).status_code == 404
    assert (await client.get(f"{P}/library", headers=auth("tok-eng-b"))).json()["items"] == []


async def test_library_private_owner_only_secrets_rejected_and_role_rules(client):
    eng = auth("tok-eng-a")
    priv = await client.post(f"{P}/library", json=body("Mine", visibility="private"), headers=eng)
    lib = priv.json()["libId"]
    # a viewer cannot save; a different engineer-level user... admin can see private, viewer cannot
    assert (
        await client.post(f"{P}/library", json=body("V"), headers=auth("tok-view-a"))
    ).status_code == 403
    assert (await client.get(f"{P}/library/{lib}", headers=auth("tok-view-a"))).status_code == 404
    assert (await client.get(f"{P}/library/{lib}", headers=auth("tok-admin-a"))).status_code == 200
    # only owner/admin may add a version or delete; the viewer cannot even see it
    assert (await client.delete(f"{P}/library/{lib}", headers=auth("tok-view-a"))).status_code in (
        403,
        404,
    )
    leak = await client.post(
        f"{P}/library",
        json=body("Leak", body={"text": "use key AKIAABCDEFGHIJKLMNOP"}),
        headers=eng,
    )
    assert leak.status_code == 422 and "AKIA" not in leak.text
    for bad in (
        {"name": "../x"},
        {"kind": "script"},
        {"body": {"text": "x", "extra": 1}},
        {"body": {"text": "x" * 4001}},
    ):
        assert (
            await client.post(f"{P}/library", json={**body("Ok"), **bad}, headers=eng)
        ).status_code == 422
    assert (await client.delete(f"{P}/library/{lib}", headers=eng)).status_code == 204
    assert (await client.get(f"{P}/library/{lib}", headers=eng)).status_code == 404


async def test_workflow_kind_validates_steps(client):
    eng = auth("tok-eng-a")
    ok = {
        "name": "Release",
        "kind": "workflow",
        "body": {
            "steps": [{"title": "Plan", "text": "Plan {x}"}, {"title": "Do", "text": "Do it"}]
        },
    }
    r = await client.post(f"{P}/library", json=ok, headers=eng)
    assert r.status_code == 201 and r.json()["params"] == ["x"]
    out = await client.post(
        f"{P}/library/{r.json()['libId']}/render", json={"values": {"x": "v"}}, headers=eng
    )
    assert [s["text"] for s in out.json()["steps"]] == ["Plan v", "Do it"]
    assert params_of({"text": "{a} {a} {b}"}) == ["a", "b"]
    too_many = {**ok, "name": "Big", "body": {"steps": [{"title": "t", "text": "x"}] * 21}}
    assert (await client.post(f"{P}/library", json=too_many, headers=eng)).status_code == 422


async def test_feedback_is_linked_scoped_scrubbed_and_upserted(client):
    sid = await new_session(client)
    h = auth("tok-eng-a")
    assert (
        await client.post(f"{P}/feedback", json={"rating": "up"}, headers=h)
    ).status_code == 422  # unlinked
    assert (
        await client.post(f"{P}/feedback", json={"rating": "meh", "sessionId": sid}, headers=h)
    ).status_code == 422
    assert (
        await client.post(f"{P}/feedback", json={"rating": "up", "runId": "run-nope"}, headers=h)
    ).status_code == 404
    assert (
        await client.post(
            f"{P}/feedback", json={"rating": "up", "sessionId": sid}, headers=auth("tok-eng-b")
        )
    ).status_code == 404
    r = await client.post(
        f"{P}/feedback",
        json={"rating": "down", "sessionId": sid, "comment": "bad ghp_" + "a" * 30 + "\x00"},
        headers=h,
    )
    assert r.status_code == 201 and "ghp_" not in r.json()["comment"]
    await client.post(
        f"{P}/feedback", json={"rating": "up", "sessionId": sid}, headers=h
    )  # same user+link: upsert
    mine = (await client.get(f"{P}/feedback", params={"sessionId": sid}, headers=h)).json()
    assert len(mine["items"]) == 1 and mine["up"] == 1 and mine["down"] == 0
    await client.post(
        f"{P}/feedback", json={"rating": "down", "sessionId": sid}, headers=auth("tok-admin-a")
    )
    assert (
        len((await client.get(f"{P}/feedback", headers=h)).json()["items"]) == 2
    )  # engineer sees team
    assert (await client.get(f"{P}/feedback", headers=auth("tok-view-a"))).json()[
        "items"
    ] == []  # viewer sees own only
    assert (await client.get(f"{P}/feedback", headers=auth("tok-eng-b"))).json()["items"] == []


async def test_feedback_by_run_id_links_only_known_runs(client):
    sid = await new_session(client)
    # run binding needs the orchestrator (not in this fixture), so bind the id directly
    client.service.store._execute(
        "UPDATE code_sessions SET run_id = 'run-7' WHERE session_id = ?", (sid,)
    )
    ok = await client.post(
        f"{P}/feedback", json={"rating": "up", "runId": "run-7"}, headers=auth("tok-eng-a")
    )
    assert ok.status_code == 201
    assert (
        await client.post(
            f"{P}/feedback", json={"rating": "up", "runId": "run-7"}, headers=auth("tok-eng-b")
        )
    ).status_code == 404
    bad = await client.post(
        f"{P}/feedback",
        json={"rating": "up", "runId": "run-7", "sessionId": "dw_other"},
        headers=auth("tok-eng-a"),
    )
    assert bad.status_code == 404


async def test_status_reports_configured_unconfigured_and_unreachable(client, upstream):
    r = await client.get(f"{P}/status", headers=auth("tok-view-a"))
    comps = {c["name"]: c["state"] for c in r.json()["components"]}
    assert comps["identity"] == "operational" and comps["orchestrator"] == "not_configured"
    assert (await client.get(f"{P}/status")).status_code == 401

    def boom(request):
        raise httpx.ConnectError("down")

    client.service.extras.http = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    comps = {
        c["name"]: c["state"]
        for c in (await client.get(f"{P}/status", headers=auth("tok-eng-a"))).json()["components"]
    }
    assert comps["identity"] == "unreachable" and comps["sandbox"] == "unreachable"
