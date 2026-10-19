"""Sessions: state machine, run links and parent/child, terminal rules, templates, resume,
deletion/retention/purge (fake clock), and the link-vs-complete race."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

import harness_identity.sessions as sessions_mod
from harness_identity.sessions import run_phase
from tests.test_sessions_support import (
    DONE,
    S,
    add_events,
    add_member,
    audit_actions,
    bearer,
    create_session,
    err,
    make_env,
    seed_project,
    seed_run,
    seed_team,
)


@pytest.fixture
async def env():
    async with make_env() as (client, factory):
        team = await seed_team(factory, ("admin@a.io", "admin"), ("eng@a.io", "engineer"),
                               ("eng2@a.io", "engineer"), ("view@a.io", "viewer"))
        other = await seed_team(factory, ("admin@b.io", "admin"), name="other")
        proj = await seed_project(factory, team["teamId"])
        yield client, factory, team, other, proj


# --------------------------------------------------------------- run phase table (004/005)


@pytest.mark.parametrize("events,claim,phase", [
    ((), None, "created"),
    (("plan_proposed",), None, "awaiting_plan_approval"),
    (("plan_proposed", "plan_approved"), None, "approved"),
    (("plan_proposed", "plan_approved"), "queued", "queued"),
    (("plan_proposed", "plan_approved", "execution_started"), "queued", "queued"),
    (("plan_proposed", "plan_approved", "execution_started"), "running", "executing"),
    (("execution_started", "paused"), None, "paused"),
    (("execution_started", "paused", "resumed"), None, "executing"),
    (("paused",), None, "created"),  # pause only applies from executing
    (("execution_started", "blocked_awaiting_clarification"), None, "blocked"),
    (("execution_started", "engine_failed"), None, "blocked"),
    (("execution_started", "engine_completed"), None, "awaiting_done_signal"),
    (("execution_started", "engine_completed", "done_signal_received"), None, "reviewing"),
    (("execution_started", "review_failed"), None, "blocked"),
    (("execution_started", "pr_opened"), None, "pr_opened"),
    (DONE, None, "done"),
    (DONE, "queued", "done"),
    # legacy loop: a review pass without the done gate keeps the run executing
    (("execution_started", "done_signal_received", "review_passed"), None, "executing"),
    # DONE is sealed: later rows never resurrect it
    (DONE + ("execution_started", "resumed", "review_failed"), None, "done"),
])
def test_run_phase_table(events, claim, phase):
    assert run_phase(list(events), claim) == phase


# --------------------------------------------------------------- state machine


async def test_transitions_roles_idempotence_and_audit(env):
    client, factory, team, _, _ = env
    eng, e2, adm = team["eng@a.io"], team["eng2@a.io"], team["admin@a.io"]
    sid = (await create_session(client, eng))["id"]
    await add_member(client, eng, sid, e2)
    obs = team["view@a.io"]
    await add_member(client, eng, sid, obs, "observer")
    url = f"{S}/{sid}"
    assert (await client.post(f"{url}/pause", headers=bearer(e2))).status_code == 403  # not leader
    assert (await client.post(f"{url}/pause", headers=bearer(obs))).status_code == 403
    await client.patch(url, json={"leaderUserId": e2["userId"]}, headers=bearer(eng))
    r = await client.post(f"{url}/pause", json={"reason": "lunch"}, headers=bearer(e2))  # leader
    assert r.json()["state"] == "paused" and r.json()["changed"] is True
    again = await client.post(f"{url}/pause", headers=bearer(e2))
    assert again.json()["changed"] is False
    (ev,) = await audit_actions(factory, "session.state_changed")
    assert ev.detail["before"] == {"state": "active"} and ev.detail["after"] == {"state": "paused"}
    assert ev.detail["reason"] == "lunch"
    # leader cannot archive or restore: owner/admin only
    assert (await client.post(f"{url}/archive", headers=bearer(e2))).status_code == 403
    r = await client.post(f"{url}/complete", headers=bearer(e2))
    assert r.json()["state"] == "completed"
    assert (await client.post(f"{url}/restore", headers=bearer(e2))).status_code == 403
    r = await client.post(f"{url}/restore", headers=bearer(eng))
    assert r.json()["state"] == "active"
    r = await client.post(f"{url}/archive", headers=bearer(adm))  # admin without membership
    assert r.json()["state"] == "archived"
    bad = await client.post(f"{url}/pause", headers=bearer(eng))
    assert bad.status_code == 409 and err(bad) == "ILLEGAL_TRANSITION"
    bad = await client.patch(url, json={"title": "Edit"}, headers=bearer(eng))
    assert bad.status_code == 409 and err(bad) == "ILLEGAL_STATE"


async def test_delete_needs_archive_reason_and_owner(env):
    client, _, team, _, _ = env
    eng, e2 = team["eng@a.io"], team["eng2@a.io"]
    sid = (await create_session(client, eng))["id"]
    await add_member(client, eng, sid, e2)
    url = f"{S}/{sid}/delete"
    r = await client.post(url, json={"reason": "cleanup"}, headers=bearer(eng))
    assert r.status_code == 409 and err(r) == "SESSION_NOT_ARCHIVED"
    await client.post(f"{S}/{sid}/archive", headers=bearer(eng))
    assert (await client.post(url, json={"reason": "x"}, headers=bearer(eng))).status_code == 422
    assert (await client.post(url, json={"reason": "cleanup"}, headers=bearer(e2))).status_code == 403  # noqa: E501
    r = await client.post(url, json={"reason": "cleanup"}, headers=bearer(eng))
    assert r.json()["state"] == "deleted"
    bad = await client.post(f"{S}/{sid}/restore", headers=bearer(team["admin@a.io"]))
    assert bad.status_code == 409 and err(bad) == "ILLEGAL_TRANSITION"  # deleted is final


# --------------------------------------------------------------- runs


async def test_link_run_team_scope_idempotence_and_exclusivity(env):
    client, factory, team, other, proj = env
    eng, e2, obs = team["eng@a.io"], team["eng2@a.io"], team["view@a.io"]
    s1 = (await create_session(client, eng, "One"))["id"]
    s2 = (await create_session(client, eng, "Two"))["id"]
    await add_member(client, eng, s1, e2)
    await add_member(client, eng, s1, obs, "observer")
    run = await seed_run(factory, proj)
    foreign_proj = await seed_project(factory, other["teamId"], "theirs")
    foreign = await seed_run(factory, foreign_proj)
    link = f"{S}/{s1}/runs"
    assert (await client.post(link, json={"runId": foreign}, headers=bearer(eng))).status_code == 404  # noqa: E501
    assert (await client.post(link, json={"runId": "nope"}, headers=bearer(eng))).status_code == 404
    assert (await client.post(link, json={"runId": run}, headers=bearer(obs))).status_code == 403
    a = await client.post(link, json={"runId": run}, headers=bearer(e2))  # participant may link
    b = await client.post(link, json={"runId": run}, headers=bearer(e2))
    assert (a.status_code, b.status_code) == (201, 200) and b.json()["created"] is False
    assert len(await audit_actions(factory, "session.run_linked")) == 1
    r = await client.post(f"{S}/{s2}/runs", json={"runId": run}, headers=bearer(eng))
    assert r.status_code == 409 and err(r) == "RUN_ALREADY_LINKED"
    runs = (await client.get(link, headers=bearer(obs))).json()["items"]
    assert [x["runId"] for x in runs] == [run] and runs[0]["phase"] == "created"
    assert runs[0]["terminal"] is False


@pytest.mark.parametrize("action,state", [("pause", "paused"), ("complete", "completed"),
                                          ("archive", "archived")])
async def test_non_active_session_refuses_new_runs(env, action, state):
    client, factory, team, _, proj = env
    eng = team["eng@a.io"]
    sid = (await create_session(client, eng))["id"]
    await client.post(f"{S}/{sid}/{action}", headers=bearer(eng))
    run = await seed_run(factory, proj)
    r = await client.post(f"{S}/{sid}/runs", json={"runId": run}, headers=bearer(eng))
    assert r.status_code == 409 and err(r) == "SESSION_NOT_ACTIVE"
    await client.post(f"{S}/{sid}/restore", headers=bearer(eng))
    assert (await client.post(f"{S}/{sid}/runs", json={"runId": run},
                              headers=bearer(eng))).status_code == 201


async def test_parent_child_links_and_unlink_rules(env):
    client, factory, team, _, proj = env
    eng, e2 = team["eng@a.io"], team["eng2@a.io"]
    sid = (await create_session(client, eng))["id"]
    await add_member(client, eng, sid, e2)
    root = await seed_run(factory, proj)
    child = await seed_run(factory, proj)
    stray = await seed_run(factory, proj)
    loose = await seed_run(factory, proj)  # exists but is not linked to the session
    link = f"{S}/{sid}/runs"
    await client.post(link, json={"runId": root}, headers=bearer(eng))
    r = await client.post(link, json={"runId": child, "parentRunId": root}, headers=bearer(eng))
    assert r.status_code == 201
    for bad_parent in (loose, stray, "ghost"):  # not in session / self / unknown
        r = await client.post(link, json={"runId": stray, "parentRunId": bad_parent},
                              headers=bearer(eng))
        assert r.status_code == 422 and err(r) == "PARENT_NOT_IN_SESSION"
    items = {x["runId"]: x for x in (await client.get(link, headers=bearer(eng))).json()["items"]}
    assert items[child]["parentRunId"] == root and items[root]["parentRunId"] is None
    r = await client.delete(f"{link}/{root}", headers=bearer(eng))
    assert r.status_code == 409 and err(r) == "RUN_HAS_CHILDREN"
    assert (await client.delete(f"{link}/{child}", headers=bearer(e2))).status_code == 403
    assert (await client.delete(f"{link}/{child}", headers=bearer(eng))).status_code == 204
    assert (await client.delete(f"{link}/{child}", headers=bearer(eng))).status_code == 404
    assert (await client.delete(f"{link}/{root}", headers=bearer(eng))).status_code == 204
    assert len(await audit_actions(factory, "session.run_unlinked")) == 2
    async with factory() as db:  # unlinking never touches the run
        assert (await db.execute(text("SELECT COUNT(*) FROM agent_runs"))).scalar() == 4
    s2 = (await create_session(client, eng, "Next"))["id"]
    assert (await client.post(f"{S}/{s2}/runs", json={"runId": root},
                              headers=bearer(eng))).status_code == 201  # free to relink


async def test_workspace_mismatch_refuses_link(env):
    client, factory, team, _, proj = env
    eng = team["eng@a.io"]
    async with factory() as db:
        await db.execute(text(
            "INSERT INTO workspaces (id, team_id, name, name_key, state, visibility,"
            " owner_user_id, created_at, updated_at) VALUES ('w1',:t,'w1','w1','active','team',"
            "'u','2026-01-01','2026-01-01')"), {"t": team["teamId"]})
        await db.commit()
    sid = (await create_session(client, eng, "Scoped", workspaceId="w1"))["id"]
    run = await seed_run(factory, proj)  # project has no workspace
    r = await client.post(f"{S}/{sid}/runs", json={"runId": run}, headers=bearer(eng))
    assert r.status_code == 409 and err(r) == "WORKSPACE_MISMATCH"
    async with factory() as db:
        await db.execute(text("UPDATE projects SET workspace_id='w1' WHERE id=:p"), {"p": proj})
        await db.commit()
    assert (await client.post(f"{S}/{sid}/runs", json={"runId": run},
                              headers=bearer(eng))).status_code == 201


async def test_complete_and_delete_wait_for_runs_to_finish(env):
    client, factory, team, _, proj = env
    eng = team["eng@a.io"]
    sid = (await create_session(client, eng))["id"]
    run = await seed_run(factory, proj, ("plan_proposed", "plan_approved", "execution_started"))
    await client.post(f"{S}/{sid}/runs", json={"runId": run}, headers=bearer(eng))
    r = await client.post(f"{S}/{sid}/complete", headers=bearer(eng))
    assert r.status_code == 409 and err(r) == "SESSION_HAS_ACTIVE_RUNS"
    assert (await client.get(f"{S}/{sid}", headers=bearer(eng))).json()["state"] == "active"
    await client.post(f"{S}/{sid}/archive", headers=bearer(eng))  # archive may proceed
    r = await client.post(f"{S}/{sid}/delete", json={"reason": "stale"}, headers=bearer(eng))
    assert r.status_code == 409 and err(r) == "SESSION_HAS_ACTIVE_RUNS"
    await add_events(factory, proj, run, "engine_completed", "done_signal_received",
                     "review_passed")
    r = await client.post(f"{S}/{sid}/delete", json={"reason": "stale"}, headers=bearer(eng))
    assert r.json()["state"] == "deleted"
    # restore + complete is fine once every run is terminal
    s2 = (await create_session(client, eng, "Two"))["id"]
    done = await seed_run(factory, proj, DONE)
    await client.post(f"{S}/{s2}/runs", json={"runId": done}, headers=bearer(eng))
    assert (await client.post(f"{S}/{s2}/complete", headers=bearer(eng))).json()["state"] == "completed"  # noqa: E501
    runs = (await client.get(f"{S}/{s2}/runs", headers=bearer(eng))).json()["items"]
    assert runs[0]["phase"] == "done" and runs[0]["terminal"] is True


async def test_race_link_vs_complete_never_leaves_live_run_in_completed_session(tmp_path):
    """Whatever the interleaving, completed + non-terminal linked run must never coexist."""
    async with make_env(str(tmp_path / "race.db")) as (client, factory):
        team = await seed_team(factory, ("eng@a.io", "engineer"))
        eng = team["eng@a.io"]
        proj = await seed_project(factory, team["teamId"])
        outcomes = set()
        for i in range(8):
            sid = (await create_session(client, eng, f"Race {i}"))["id"]
            run = await seed_run(factory, proj, ("execution_started",))
            link, done = await asyncio.gather(
                client.post(f"{S}/{sid}/runs", json={"runId": run}, headers=bearer(eng)),
                client.post(f"{S}/{sid}/complete", headers=bearer(eng)))
            state = (await client.get(f"{S}/{sid}", headers=bearer(eng))).json()["state"]
            linked = (await client.get(f"{S}/{sid}/runs", headers=bearer(eng))).json()["items"]
            assert not (state == "completed" and linked), (i, link.text, done.text)
            outcomes.add((link.status_code, done.status_code))
        assert outcomes  # at least ran; each pair is (201,409) or (409,200)
        assert all(o in {(201, 409), (409, 200)} for o in outcomes), outcomes


# --------------------------------------------------------------- retention / purge


async def test_delete_retention_and_purge_semantics(env, monkeypatch):
    client, factory, team, _, proj = env
    eng, e2, adm = team["eng@a.io"], team["eng2@a.io"], team["admin@a.io"]
    t0 = datetime(2026, 3, 1, tzinfo=UTC)
    clock = {"now": t0}
    monkeypatch.setattr(sessions_mod, "now_utc", lambda: clock["now"])
    sid = (await create_session(client, eng, "Gone soon", retentionDays=10))["id"]
    await add_member(client, eng, sid, e2)
    run = await seed_run(factory, proj, DONE)
    await client.post(f"{S}/{sid}/runs", json={"runId": run}, headers=bearer(eng))
    await client.post(f"{S}/{sid}/archive", headers=bearer(eng))
    d = await client.post(f"{S}/{sid}/delete", json={"reason": "retire it"}, headers=bearer(eng))
    assert d.json()["purgeAfter"].startswith("2026-03-11")
    # soft-deleted: invisible to its members and the list, visible to admin only
    assert (await client.get(f"{S}/{sid}", headers=bearer(e2))).status_code == 404
    assert (await client.get(f"{S}/{sid}", headers=bearer(eng))).status_code == 404
    assert sid not in [i["id"] for i in (await client.get(S, headers=bearer(eng))).json()["items"]]
    assert (await client.get(f"{S}/{sid}", headers=bearer(adm))).json()["state"] == "deleted"
    # purge: admin only, state deleted, retention elapsed
    assert (await client.post(f"{S}/{sid}/purge", headers=bearer(eng))).status_code == 403
    clock["now"] = t0 + timedelta(days=9, hours=23)
    r = await client.post(f"{S}/{sid}/purge", headers=bearer(adm))
    assert r.status_code == 409 and err(r) == "RETENTION_NOT_ELAPSED"
    clock["now"] = t0 + timedelta(days=10, seconds=1)
    r = await client.post(f"{S}/{sid}/purge", headers=bearer(adm))
    assert r.status_code == 200 and r.json()["changed"] is True
    assert r.json()["title"] == "[purged]" and r.json()["purgedAt"]
    again = await client.post(f"{S}/{sid}/purge", headers=bearer(adm))
    assert again.json()["changed"] is False
    (ev,) = await audit_actions(factory, "session.purged")
    assert ev.detail["membersErased"] == 2 and ev.detail["runsRetained"] == 1
    async with factory() as db:  # runs preserved, link kept so the gate keeps refusing starts
        assert (await db.execute(text("SELECT COUNT(*) FROM agent_runs"))).scalar() == 1
        assert (await db.execute(text("SELECT COUNT(*) FROM session_runs"))).scalar() == 1
        assert (await db.execute(text("SELECT COUNT(*) FROM session_members"))).scalar() == 0
    # an active session cannot be purged
    live = (await create_session(client, eng, "Live"))["id"]
    r = await client.post(f"{S}/{live}/purge", headers=bearer(adm))
    assert r.status_code == 409 and err(r) == "SESSION_NOT_DELETED"


async def test_default_retention_from_env(env, monkeypatch):
    client, _, team, _, _ = env
    monkeypatch.setenv("HARNESS_SESSION_RETENTION_DAYS", "7")
    s = await create_session(client, team["eng@a.io"], "Env")
    assert s["retentionDays"] == 7


# --------------------------------------------------------------- templates


async def test_templates_versions_apply_and_create(env):
    client, factory, team, other, _ = env
    eng, adm, vw = team["eng@a.io"], team["admin@a.io"], team["view@a.io"]
    body = {"name": "triage", "defaults": {"description": "Weekly triage", "retentionDays": 14,
                                           "agentIds": ["triage-bot", "scribe", "triage-bot"]}}
    assert (await client.post(f"{S}/templates", json=body, headers=bearer(vw))).status_code == 403
    v1 = await client.post(f"{S}/templates", json=body, headers=bearer(eng))
    assert v1.status_code == 201 and v1.json()["version"] == 1
    assert v1.json()["defaults"]["agentIds"] == ["scribe", "triage-bot"]
    body["defaults"]["retentionDays"] = 21
    v2 = await client.post(f"{S}/templates", json=body, headers=bearer(adm))
    assert v2.json()["version"] == 2
    for bad in ({"x": 1}, {"retentionDays": 0}, {"retentionDays": True}, {"agentIds": ["bad id"]},
                {"agentIds": ["a"] * 11}, {"description": "x" * 1001}):
        r = await client.post(f"{S}/templates", json={"name": "bad", "defaults": bad},
                              headers=bearer(eng))
        assert r.status_code == (422), bad
    assert len(await audit_actions(factory, "session.template_saved")) == 2
    listed = (await client.get(f"{S}/templates", headers=bearer(vw))).json()["items"]
    assert [(t["name"], t["version"]) for t in listed] == [("triage", 1), ("triage", 2)]
    assert (await client.get(f"{S}/templates", headers=bearer(other["admin@b.io"]))).json()["items"] == []  # noqa: E501
    # create from the pinned version 1, then apply the latest
    s = await client.post(S, json={"title": "Mon", "templateName": "triage", "templateVersion": 1},
                          headers=bearer(eng))
    assert s.status_code == 201
    assert s.json()["template"] == {"name": "triage", "version": 1}
    assert s.json()["retentionDays"] == 14 and s.json()["description"] == "Weekly triage"
    sid = s.json()["id"]
    roster = (await client.get(f"{S}/{sid}/members", headers=bearer(eng))).json()["items"]
    assert sorted(m["agentId"] for m in roster if m["kind"] == "agent") == ["scribe", "triage-bot"]
    r = await client.post(f"{S}/{sid}/apply-template", json={"name": "triage"}, headers=bearer(eng))
    assert r.json()["template"]["version"] == 2 and r.json()["retentionDays"] == 21
    roster = (await client.get(f"{S}/{sid}/members", headers=bearer(eng))).json()["items"]
    assert len([m for m in roster if m["kind"] == "agent"]) == 2  # idempotent, no duplicates
    evs = await audit_actions(factory, "session.template_applied")
    assert [e.detail["at"] for e in evs] == ["create", "apply"]
    assert evs[1].detail["before"]["retentionDays"] == 14 and evs[1].detail["after"]["retentionDays"] == 21  # noqa: E501
    miss = await client.post(f"{S}/{sid}/apply-template", json={"name": "ghost"}, headers=bearer(eng))  # noqa: E501
    assert miss.status_code == 404
    cross = await client.post(S, json={"title": "Xx", "templateName": "triage"},
                              headers=bearer(other["admin@b.io"]))
    assert cross.status_code == 404  # templates are team-scoped


# --------------------------------------------------------------- resume


async def test_resume_returns_history_runs_members_permissions_policy(env):
    client, factory, team, other, proj = env
    eng, e2, obs, adm = team["eng@a.io"], team["eng2@a.io"], team["view@a.io"], team["admin@a.io"]
    sid = (await create_session(client, eng, "Resumable"))["id"]
    await add_member(client, eng, sid, e2)
    await add_member(client, eng, sid, obs, "observer")
    run = await seed_run(factory, proj, ("execution_started",))
    await client.post(f"{S}/{sid}/runs", json={"runId": run}, headers=bearer(eng))
    await client.post(f"{S}/{sid}/pause", headers=bearer(eng))
    await client.delete(f"{S}/{sid}/members/{e2['userId']}", headers=bearer(e2))
    r = await client.get(f"{S}/{sid}/resume", headers=bearer(eng))
    assert r.status_code == 200
    body = r.json()
    actions = [h["action"] for h in body["history"]]
    assert actions[:3] == ["session.member_left", "session.state_changed", "session.run_linked"]
    assert {"session.created", "session.run_linked", "session.state_changed",
            "session.member_left"} <= set(actions)
    assert body["history"][0]["actor"] == "eng2"  # actor resolved from the audit actor id
    assert [x["runId"] for x in body["runs"]] == [run] and body["runs"][0]["phase"] == "executing"
    statuses = {m["userId"]: m["status"] for m in body["members"]}
    assert statuses[e2["userId"]] == "left"  # history preserved in the resume payload
    assert body["policy"]["state"] == "paused" and body["policy"]["launchAllowed"] is False
    assert body["policy"]["launchBlockedBy"] == "SESSION_NOT_ACTIVE"
    perms = body["you"]["permissions"]
    assert perms["manageMembers"] is True and perms["linkRuns"] is False  # paused
    assert perms["delete"] is False and perms["purge"] is False
    ob = (await client.get(f"{S}/{sid}/resume", headers=bearer(obs))).json()["you"]
    assert ob["memberRole"] == "observer"
    assert ob["permissions"]["changeState"] is False and ob["permissions"]["manageMembers"] is False
    await client.post(f"{S}/{sid}/restore", headers=bearer(eng))
    pol = (await client.get(f"{S}/{sid}/resume", headers=bearer(eng))).json()
    assert pol["policy"]["launchAllowed"] is True and pol["you"]["permissions"]["linkRuns"] is True
    ad = (await client.get(f"{S}/{sid}/resume", headers=bearer(adm))).json()["you"]
    assert ad["memberRole"] is None and ad["permissions"]["manageMembers"] is True
    assert len(await audit_actions(factory, "session.resumed")) == 4
    assert (await client.get(f"{S}/{sid}/resume", headers=bearer(e2))).status_code == 404  # left
    assert (await client.get(f"{S}/{sid}/resume",
                             headers=bearer(other["admin@b.io"]))).status_code == 404


async def test_resume_reports_workspace_state(env):
    client, factory, team, _, _ = env
    eng = team["eng@a.io"]
    async with factory() as db:
        await db.execute(text(
            "INSERT INTO workspaces (id, team_id, name, name_key, state, visibility,"
            " owner_user_id, created_at, updated_at) VALUES ('w9',:t,'w9','w9','active','team',"
            "'u','2026-01-01','2026-01-01')"), {"t": team["teamId"]})
        await db.commit()
    sid = (await create_session(client, eng, "WS", workspaceId="w9"))["id"]
    async with factory() as db:
        await db.execute(text("UPDATE workspaces SET state='archived' WHERE id='w9'"))
        await db.commit()
    pol = (await client.get(f"{S}/{sid}/resume", headers=bearer(eng))).json()["policy"]
    assert pol["workspace"] == {"id": "w9", "state": "archived"}
    assert pol["launchAllowed"] is False and pol["launchBlockedBy"] == "WORKSPACE_NOT_ACTIVE"
