"""Part 44.004 (subtasks, dependencies, blockers, evidence), 44.005 (limits API),
44.007/44.008 (reviewable outputs and human-accepted completion). Sqlite with real migrations
0041 and 0048, a real OrchestratorService, a fake clock. Behaviour: docs/execution.md."""

import asyncio
import json

import pytest
from sqlalchemy.exc import IntegrityError

from harness_orchestrator import execution
from harness_orchestrator.state_machine import AgentState

from .test_execution_support import RESERVED, err
from .test_execution_support import env as env  # the shared fixture

R = "/runs/r1"


# ----------------------------------------------------------- isolation, no oracle

ROUTES = (
    ("GET", "/subtasks", None),
    ("POST", "/subtasks", {"title": "x"}),
    ("PATCH", "/subtasks/s", {"expectedVersion": 1, "title": "y"}),
    ("POST", "/subtasks/s/block", {"reason": "because"}),
    ("POST", "/subtasks/s/unblock", {}),
    ("POST", "/subtasks/s/complete", {"expectedVersion": 1}),
    ("POST", "/subtasks/s/evidence", {"kind": "note", "ref": "n"}),
    ("GET", "/execution-limits", None),
    ("PUT", "/execution-limits", {"maxSeconds": 5}),
    ("GET", "/execution-status", None),
    ("GET", "/completion", None),
    ("POST", "/completion/report", {"summary": "s"}),
    ("POST", "/completion/accept", {"reportDigest": "a" * 64}),
    ("POST", "/completion/reject", {"reportDigest": "a" * 64, "reason": "nope"}),
    ("POST", "/abandon", {"reason": "no longer needed"}),
    ("POST", "/retry", {"requestId": "req-12345678"}),
)


async def test_foreign_and_unknown_runs_answer_the_same_404_on_every_route(env):
    await env.new_run("r1")
    await env.new_run("rb", "team-b")
    for method, tail, body in ROUTES:
        kw = {"json": body} if body is not None else {}
        unknown = await env.call("lead", method, f"/runs/nope{tail}", **kw)
        foreign = await env.call("lead", method, f"/runs/rb{tail}", **kw)
        assert unknown.status_code == 404, (method, tail, unknown.text)
        assert foreign.json() == unknown.json(), (method, tail)
    for table in ("run_subtasks", "run_execution_limits", "run_completion", "run_attempts"):
        assert await env.q(f"SELECT 1 FROM {table}") == []


async def test_role_gate_comes_first_and_never_tells_runs_apart(env):
    await env.new_run("r1")
    await env.new_run("rb", "team-b")
    writes = [r for r in ROUTES if r[0] != "GET"]
    for user in ("view", "guest"):
        for method, tail, body in writes:
            for rid in ("r1", "rb", "nope"):
                resp = await env.call(user, method, f"/runs/{rid}{tail}", json=body)
                err(resp, 403, "ROLE_DENIED")
        for method, tail, _ in ROUTES:
            if method == "GET":
                assert (await env.call(user, "GET", f"/runs/r1{tail}")).status_code == 200
    assert any(r.action == "authz.denied" for r in await env.audit())


async def test_subtask_ids_of_another_run_are_not_found_and_dependencies_cannot_cross_runs(env):
    await env.new_run("r1")
    await env.new_run("r2")
    other = await env.sub("r2", "elsewhere")
    mine = await env.sub("r1", "mine")
    url = f"{R}/subtasks/{other['id']}"
    err(
        await env.post("lead", url + "/evidence", {"kind": "note", "ref": "n"}),
        404,
        "SUBTASK_NOT_FOUND",
    )
    err(await env.post("lead", url + "/complete", {"expectedVersion": 1}), 404, "SUBTASK_NOT_FOUND")
    unknown = await env.post("lead", f"{R}/subtasks", {"title": "x", "dependsOn": ["nope"]})
    cross = await env.post("lead", f"{R}/subtasks", {"title": "x", "dependsOn": [other["id"]]})
    assert err(cross, 422, "BAD_DEPENDENCY") == err(unknown, 422, "BAD_DEPENDENCY")
    assert (await env.get("lead", f"{R}/subtasks")).json()["summary"]["total"] == 1
    assert mine["dependsOn"] == []


# --------------------------------------------------------------------- subtasks


async def test_create_list_dependencies_and_ready(env):
    await env.new_run("r1")
    a = await env.sub("r1", "write the parser")
    b = await env.sub("r1", "tests", dependsOn=[a["id"]], description="  after the parser ")
    assert b["dependsOn"] == [a["id"]] and b["unmetDependencies"] == [a["id"]]
    assert b["description"] == "after the parser" and b["state"] == "todo"
    listing = (await env.get("view", f"{R}/subtasks")).json()
    ready = {s["title"]: s["ready"] for s in listing["subtasks"]}
    assert ready == {"write the parser": True, "tests": False}
    assert listing["summary"]["total"] == 2 and listing["summary"]["byState"]["todo"] == 2
    assert [s["position"] for s in listing["subtasks"]] == [1, 2]


async def test_dependency_cycles_are_refused(env):
    await env.new_run("r1")
    a = await env.sub("r1", "a")
    b = await env.sub("r1", "b", dependsOn=[a["id"]])
    c = await env.sub("r1", "c", dependsOn=[b["id"]])
    for sub, dep in ((a, [a["id"]]), (a, [b["id"]]), (a, [c["id"]]), (b, [c["id"]])):
        r = await env.call(
            "lead",
            "PATCH",
            f"{R}/subtasks/{sub['id']}",
            json={"expectedVersion": sub["version"], "dependsOn": dep},
        )
        err(r, 409, "DEPENDENCY_CYCLE")
    rows = await env.q("SELECT subtask_id, depends_on_id FROM run_subtask_deps")
    assert {(r.subtask_id, r.depends_on_id) for r in rows} == {
        (b["id"], a["id"]),
        (c["id"], b["id"]),
    }
    # a legal re-wire is fine, and replaces the edges
    ok = await env.call(
        "lead",
        "PATCH",
        f"{R}/subtasks/{c['id']}",
        json={"expectedVersion": c["version"], "dependsOn": [a["id"]]},
    )
    assert ok.status_code == 200 and ok.json()["subtask"]["dependsOn"] == [a["id"]]


async def test_complete_needs_evidence_finished_dependencies_and_no_blockers(env):
    await env.new_run("r1")
    a = await env.sub("r1", "a")
    b = await env.sub("r1", "b", dependsOn=[a["id"]])
    done = lambda s: f"{R}/subtasks/{s['id']}/complete"  # noqa: E731
    err(await env.post("lead", done(a), {"expectedVersion": 1}), 409, "EVIDENCE_REQUIRED")
    await env.post("lead", f"{R}/subtasks/{b['id']}/evidence", {"kind": "diff", "ref": "d-1"})
    deps = err(
        await env.post("lead", done(b), {"expectedVersion": 1}), 409, "DEPENDENCIES_INCOMPLETE"
    )
    assert deps["unmetDependencies"] == [a["id"]]
    await env.post("lead", f"{R}/subtasks/{a['id']}/evidence", {"kind": "test", "ref": "t::a"})
    await env.post("lead", f"{R}/subtasks/{a['id']}/block", {"reason": "waiting for review"})
    err(await env.post("lead", done(a), {"expectedVersion": 2}), 409, "SUBTASK_BLOCKED")
    await env.post("lead", f"{R}/subtasks/{a['id']}/unblock", {})
    cur = (await env.get("lead", f"{R}/subtasks")).json()["subtasks"][0]
    r = await env.post("lead", done(a), {"expectedVersion": cur["version"]})
    assert r.status_code == 200 and r.json()["subtask"]["state"] == "done"
    assert r.json()["subtask"]["completedBy"] == "lead" and not r.json()["duplicate"]
    ok = await env.post("lead", done(b), {"expectedVersion": 1})
    assert ok.status_code == 200 and ok.json()["subtask"]["unmetDependencies"] == []


async def test_complete_is_idempotent_and_stale_versions_are_refused(env):
    await env.new_run("r1")
    s = await env.sub("r1", "a")
    base = f"{R}/subtasks/{s['id']}"
    await env.post("lead", base + "/evidence", {"kind": "output", "ref": "log-1"})
    err(await env.post("lead", base + "/complete", {"expectedVersion": 99}), 409, "STALE_VERSION")
    first = await env.post("lead", base + "/complete", {"expectedVersion": 1})
    again = await env.post("eng2", base + "/complete", {"expectedVersion": 1})
    assert first.json()["duplicate"] is False and again.json()["duplicate"] is True
    assert again.json()["subtask"]["completedBy"] == "lead"
    assert len(await env.audit("subtask.completed")) == 1
    err(
        await env.call("lead", "PATCH", base, json={"expectedVersion": 2, "title": "x"}),
        409,
        "SUBTASK_DONE",
    )


async def test_block_and_unblock_restore_the_previous_state(env):
    await env.new_run("r1")
    s = await env.sub("r1", "a")
    base = f"{R}/subtasks/{s['id']}"
    ok = await env.call("lead", "PATCH", base, json={"expectedVersion": 1, "state": "in_progress"})
    assert ok.json()["subtask"]["state"] == "in_progress"
    one = (await env.post("lead", base + "/block", {"reason": "needs credentials"})).json()
    again = (await env.post("lead", base + "/block", {"reason": "needs credentials"})).json()
    assert one["subtask"]["state"] == "blocked" and again["duplicate"] is True
    assert len(again["subtask"]["blockers"]) == 1
    two = (await env.post("lead", base + "/block", {"reason": "and a review"})).json()
    first, second = (b["id"] for b in two["subtask"]["blockers"])
    err(
        await env.call(
            "lead",
            "PATCH",
            base,
            json={"expectedVersion": two["subtask"]["version"], "state": "todo"},
        ),
        409,
        "SUBTASK_BLOCKED",
    )
    part = (await env.post("lead", base + "/unblock", {"blockerId": first})).json()
    assert part["subtask"]["state"] == "blocked" and len(part["subtask"]["blockers"]) == 1
    full = (
        await env.post("lead", base + "/unblock", {"blockerId": second, "resolution": "done"})
    ).json()
    assert full["subtask"]["state"] == "in_progress" and full["subtask"]["blockers"] == []
    nothing = (await env.post("lead", base + "/unblock", {})).json()
    assert nothing["duplicate"] is True
    err(await env.post("lead", base + "/unblock", {"blockerId": "nope"}), 404, "BLOCKER_NOT_FOUND")
    kept = await env.q(
        "SELECT state, resolved_by FROM run_subtask_blockers ORDER BY created_at, id"
    )
    assert [(k.state, k.resolved_by) for k in kept] == [("resolved", "lead")] * 2  # rows are kept


async def test_evidence_validation_dedup_and_cap(env, monkeypatch):
    await env.new_run("r1")
    s = await env.sub("r1", "a")
    url = f"{R}/subtasks/{s['id']}/evidence"
    for bad in (
        {"kind": "file", "ref": "../etc/passwd"},
        {"kind": "file", "ref": "/abs/path"},
        {"kind": "link", "ref": "javascript:alert(1)"},
        {"kind": "link", "ref": "http://a b"},
    ):
        assert (await env.post("lead", url, bad)).status_code == 422, bad
    assert (await env.post("lead", url, {"kind": "bogus", "ref": "x"})).status_code == 422
    ok = await env.post("lead", url, {"kind": "file", "ref": "src/app.py", "note": "the fix"})
    dup = await env.post("lead", url, {"kind": "file", "ref": "src/app.py"})
    assert ok.json()["duplicate"] is False and dup.json()["duplicate"] is True
    assert dup.json()["subtask"]["evidenceCount"] == 1
    assert (
        await env.post("lead", url, {"kind": "link", "ref": "https://ci.example/run/7"})
    ).status_code == 200
    monkeypatch.setattr(execution, "MAX_EVIDENCE", 2)
    err(await env.post("lead", url, {"kind": "note", "ref": "third"}), 409, "EVIDENCE_LIMIT")


async def test_size_caps_and_subtask_count_cap(env, monkeypatch):
    await env.new_run("r1")
    assert (await env.post("lead", f"{R}/subtasks", {"title": "x" * 201})).status_code == 422
    assert (await env.post("lead", f"{R}/subtasks", {"title": "  "})).status_code == 422
    assert (
        await env.post("lead", f"{R}/subtasks", {"title": "x", "description": "d" * 2001})
    ).status_code == 422
    assert (
        await env.post("lead", f"{R}/subtasks", {"title": "x", "dependsOn": ["a"] * 21})
    ).status_code == 422
    monkeypatch.setattr(execution, "MAX_SUBTASKS", 2)
    await env.sub("r1", "1")
    await env.sub("r1", "2")
    err(await env.post("lead", f"{R}/subtasks", {"title": "3"}), 409, "SUBTASK_LIMIT")


async def test_patch_is_versioned_and_a_noop_changes_nothing(env):
    await env.new_run("r1")
    s = await env.sub("r1", "a")
    base = f"{R}/subtasks/{s['id']}"
    ok = await env.call("lead", "PATCH", base, json={"expectedVersion": 1, "title": "renamed"})
    assert ok.json()["subtask"]["version"] == 2
    stale = await env.call("eng2", "PATCH", base, json={"expectedVersion": 1, "title": "mine"})
    assert err(stale, 409, "STALE_VERSION")["currentVersion"] == 2
    noop = await env.call("lead", "PATCH", base, json={"expectedVersion": 2, "title": "renamed"})
    assert noop.json()["subtask"]["version"] == 2
    assert len(await env.audit("subtask.updated")) == 1
    err(
        await env.post("lead", f"{R}/subtasks/{s['id']}/unblock", {"blockerId": "x"}),
        404,
        "BLOCKER_NOT_FOUND",
    )


async def test_start_requires_finished_dependencies(env):
    await env.new_run("r1")
    a = await env.sub("r1", "a")
    b = await env.sub("r1", "b", dependsOn=[a["id"]])
    r = await env.call(
        "lead",
        "PATCH",
        f"{R}/subtasks/{b['id']}",
        json={"expectedVersion": 1, "state": "in_progress"},
    )
    assert err(r, 409, "DEPENDENCIES_INCOMPLETE")["unmetDependencies"] == [a["id"]]
    await env.post("lead", f"{R}/subtasks/{a['id']}/evidence", {"kind": "note", "ref": "x"})
    await env.post("lead", f"{R}/subtasks/{a['id']}/complete", {"expectedVersion": 1})
    ok = await env.call(
        "lead",
        "PATCH",
        f"{R}/subtasks/{b['id']}",
        json={"expectedVersion": 1, "state": "in_progress"},
    )
    assert ok.status_code == 200


async def test_subtask_audit_rows_carry_ids_and_states_never_text(env):
    await env.new_run("r1")
    s = await env.sub("r1", "SECRET TITLE", description="SECRET BODY")
    base = f"{R}/subtasks/{s['id']}"
    await env.post("lead", base + "/block", {"reason": "SECRET REASON"})
    await env.post("lead", base + "/unblock", {})
    await env.post("lead", base + "/evidence", {"kind": "note", "ref": "SECRET REF"})
    rows = [r for r in await env.audit() if r.action.startswith("subtask.")]
    assert {r.action for r in rows} == {
        "subtask.created",
        "subtask.blocked",
        "subtask.unblocked",
        "subtask.evidence_added",
    }
    assert {r.action for r in rows} <= RESERVED
    blob = " ".join(r.detail for r in rows)
    assert "SECRET" not in blob and s["id"] in blob
    d = json.loads(rows[0].detail)
    assert d["userId"] == "lead" and d["teamId"] == "team-a" and d["runId"] == "r1"
    kinds = [r.kind for r in await env.q("SELECT kind FROM run_collab_events ORDER BY seq")]
    assert "subtask.created" in kinds and "subtask.blocked" in kinds


async def test_race_two_patches_with_one_version_only_one_wins(env):
    await env.new_run("r1")
    s = await env.sub("r1", "a")
    url = f"{R}/subtasks/{s['id']}"
    res = await asyncio.gather(
        env.call("lead", "PATCH", url, json={"expectedVersion": 1, "title": "one"}),
        env.call("lead", "PATCH", url, json={"expectedVersion": 1, "title": "two"}),
    )
    assert sorted(r.status_code for r in res) == [200, 409]
    assert (await env.q("SELECT version FROM run_subtasks"))[0].version == 2


async def test_race_same_request_id_creates_one_subtask(env):
    await env.new_run("r1")
    res = await asyncio.gather(
        *[
            env.post("lead", f"{R}/subtasks", {"title": "once", "requestId": "same-req"})
            for _ in range(5)
        ]
    )
    assert sorted(r.status_code for r in res) == [200, 200, 200, 200, 201]
    assert len({r.json()["subtask"]["id"] for r in res}) == 1
    assert len(await env.q("SELECT 1 FROM run_subtasks")) == 1
    assert len(await env.audit("subtask.created")) == 1


async def test_race_two_blocks_with_the_same_reason_leave_one_blocker(env):
    await env.new_run("r1")
    s = await env.sub("r1", "a")
    res = await asyncio.gather(
        *[
            env.post("lead", f"{R}/subtasks/{s['id']}/block", {"reason": "same reason"})
            for _ in range(2)
        ]
    )
    assert all(r.status_code == 200 for r in res)
    assert len(await env.q("SELECT 1 FROM run_subtask_blockers WHERE state = 'open'")) == 1
    assert sorted(r.json()["duplicate"] for r in res) == [False, True]
    assert (await env.q("SELECT version FROM run_subtasks"))[0].version == 2


# ------------------------------------------------------------------- limits API


async def test_limits_default_put_versioning_noop_and_audit(env):
    await env.new_run("r1")
    got = (await env.get("view", f"{R}/execution-limits")).json()
    assert got["limits"]["isDefault"] and got["limits"]["version"] == 0
    assert got["enforcement"]["toolCalls"]["enforced"] is False
    assert got["enforcement"]["seconds"]["enforced"] is True
    put = await env.call(
        "lead",
        "PUT",
        f"{R}/execution-limits",
        json={"maxSeconds": 600, "maxToolCalls": 40, "expectedVersion": 0},
    )
    lim = put.json()["limits"]
    assert (lim["maxSeconds"], lim["maxToolCalls"], lim["maxModelCalls"], lim["version"]) == (
        600,
        40,
        None,
        1,
    )
    err(
        await env.call(
            "lead", "PUT", f"{R}/execution-limits", json={"maxSeconds": 9, "expectedVersion": 0}
        ),
        409,
        "STALE_VERSION",
    )
    same = await env.call(
        "lead",
        "PUT",
        f"{R}/execution-limits",
        json={"maxSeconds": 600, "maxToolCalls": 40, "expectedVersion": 1},
    )
    assert same.json()["limits"]["version"] == 1
    up = await env.call(
        "eng2",
        "PUT",
        f"{R}/execution-limits",
        json={"maxSeconds": 900, "maxCostMicrousd": 5_000_000, "expectedVersion": 1},
    )
    assert up.json()["limits"]["version"] == 2 and up.json()["limits"]["maxToolCalls"] is None
    rows = await env.audit("execution.limits_updated")
    assert len(rows) == 2
    detail = json.loads(rows[1].detail)
    assert detail["before"]["maxSeconds"] == 600 and detail["after"]["maxSeconds"] == 900
    assert detail["userId"] == "eng2"


@pytest.mark.parametrize(
    "body",
    [
        {"maxSeconds": 0},
        {"maxSeconds": 604_801},
        {"maxModelCalls": -1},
        {"maxToolCalls": 1_000_001},
        {"maxCostMicrousd": 0},
    ],
)
async def test_limits_outside_the_bounds_are_refused(env, body):
    await env.new_run("r1")
    assert (await env.call("lead", "PUT", f"{R}/execution-limits", json=body)).status_code == 422
    assert await env.q("SELECT 1 FROM run_execution_limits") == []


async def test_race_first_limits_writers_only_one_wins(env):
    await env.new_run("r1")
    res = await asyncio.gather(
        *[
            env.call(
                "lead",
                "PUT",
                f"{R}/execution-limits",
                json={"maxSeconds": 100 + i, "expectedVersion": 0},
            )
            for i in range(4)
        ]
    )
    assert sorted(r.status_code for r in res) == [200, 409, 409, 409]
    assert (await env.q("SELECT version FROM run_execution_limits"))[0].version == 1


# ------------------------------------------------------------------- completion


async def test_agent_report_is_not_human_acceptance(env):
    await env.new_run("r1")
    before = (await env.get("lead", f"{R}/completion")).json()
    assert before["agentReported"]["state"] == "none" and before["accepted"] is False
    assert before["review"]["blocking"][0]["code"] == "NO_REPORT"
    out = {"kind": "diff", "ref": "d-1", "note": "the change"}
    rep = await env.report("r1", "lead", "implemented", outputs=[out])
    assert rep["agentReported"]["state"] == "reported" and rep["agentReported"]["reportNo"] == 1
    assert rep["humanDecision"]["state"] == "none" and rep["accepted"] is False
    assert rep["agentReported"]["outputs"] == [{"kind": "diff", "ref": "d-1", "note": "the change"}]
    assert rep["review"]["canAccept"] is False  # the reporter may not accept
    other = (await env.get("eng2", f"{R}/completion")).json()
    assert other["review"]["canAccept"] is True and other["review"]["canReject"] is True
    viewer = (await env.get("view", f"{R}/completion")).json()
    assert viewer["review"]["canAccept"] is False and viewer["accepted"] is False


async def test_only_a_different_approver_can_accept_with_the_reviewed_digest(env):
    await env.new_run("r1")
    rep = await env.report("r1", "lead")
    digest = rep["agentReported"]["digest"]
    me = await env.post("lead", f"{R}/completion/accept", {"reportDigest": digest})
    err(me, 403, "SELF_ACCEPT_FORBIDDEN")
    err(
        await env.post("view", f"{R}/completion/accept", {"reportDigest": digest}),
        403,
        "ROLE_DENIED",
    )
    err(
        await env.post("guest", f"{R}/completion/accept", {"reportDigest": digest}),
        403,
        "ROLE_DENIED",
    )
    err(
        await env.post("eng2", f"{R}/completion/accept", {"reportDigest": "b" * 64}),
        409,
        "STALE_REPORT",
    )
    assert (await env.get("eng2", f"{R}/completion")).json()["accepted"] is False
    assert any(
        r.action == "authz.denied" and "SELF_ACCEPT_FORBIDDEN" in r.detail
        for r in await env.audit()
    )
    ok = await env.post(
        "eng2", f"{R}/completion/accept", {"reportDigest": digest, "reason": "looks right"}
    )
    body = ok.json()
    assert ok.status_code == 200 and body["accepted"] is True and body["duplicate"] is False
    assert body["humanDecision"] == {
        "state": "accepted",
        "decidedBy": "eng2",
        "decidedAt": body["humanDecision"]["decidedAt"],
        "reason": "looks right",
        "digest": digest,
    }
    again = await env.post("eng2", f"{R}/completion/accept", {"reportDigest": digest})
    assert again.json()["duplicate"] is True and len(await env.audit("completion.accepted")) == 1
    err(
        await env.post(
            "boss", f"{R}/completion/reject", {"reportDigest": digest, "reason": "too late"}
        ),
        409,
        "ALREADY_DECIDED",
    )


async def test_a_new_report_invalidates_the_old_digest_and_a_rejection_starts_a_new_round(env):
    await env.new_run("r1")
    first = (await env.report("r1", "lead", "v1"))["agentReported"]["digest"]
    short = await env.post("eng2", f"{R}/completion/reject", {"reportDigest": first, "reason": "n"})
    assert short.status_code == 422  # a reason is required
    rej = await env.post(
        "eng2", f"{R}/completion/reject", {"reportDigest": first, "reason": "tests missing"}
    )
    assert rej.json()["humanDecision"]["state"] == "rejected" and rej.json()["accepted"] is False
    err(
        await env.post("boss", f"{R}/completion/accept", {"reportDigest": first}),
        409,
        "ALREADY_DECIDED",
    )
    second = await env.report("r1", "lead", "v2 with tests")
    assert second["agentReported"]["reportNo"] == 2 and second["humanDecision"]["state"] == "none"
    assert second["agentReported"]["digest"] != first
    err(
        await env.post("boss", f"{R}/completion/accept", {"reportDigest": first}),
        409,
        "STALE_REPORT",
    )
    ok = await env.post(
        "boss", f"{R}/completion/accept", {"reportDigest": second["agentReported"]["digest"]}
    )
    assert ok.json()["accepted"] is True
    history = ok.json()["history"]
    assert [(h["reportNo"], h["event"]) for h in history] == [
        (1, "reported"),
        (1, "rejected"),
        (2, "reported"),
        (2, "accepted"),
    ]


async def test_accept_needs_a_report_a_settled_engine_and_finished_subtasks(env):
    await env.new_run("r1")
    err(
        await env.post("eng2", f"{R}/completion/accept", {"reportDigest": "c" * 64}),
        409,
        "NO_REPORT",
    )
    sub = await env.sub("r1", "unfinished")
    digest = (await env.report("r1", "lead"))["agentReported"]["digest"]
    full = err(
        await env.post("eng2", f"{R}/completion/accept", {"reportDigest": digest}),
        409,
        "SUBTASKS_INCOMPLETE",
    )
    assert full["blocking"][0]["subtaskIds"] == [sub["id"]]
    cancel = await env.call(
        "lead",
        "PATCH",
        f"{R}/subtasks/{sub['id']}",
        json={"expectedVersion": 1, "state": "cancelled"},
    )
    assert cancel.status_code == 200
    env.svc.runs["r1"]["state_machine"].state = AgentState.EXECUTING
    err(
        await env.post("eng2", f"{R}/completion/accept", {"reportDigest": digest}),
        409,
        "RUN_EXECUTING",
    )
    env.svc.runs["r1"]["state_machine"].state = AgentState.BLOCKED
    assert (
        await env.post("eng2", f"{R}/completion/accept", {"reportDigest": digest})
    ).status_code == 200


async def test_the_database_itself_refuses_a_self_accepted_or_reportless_acceptance(env):
    await env.new_run("r1")
    await env.report("r1", "lead")
    now = env.clock["t"]
    with pytest.raises(IntegrityError):
        await env.x(
            "UPDATE run_completion SET human_state='accepted', decided_by='lead', decided_at=:n",
            n=now,
        )
    with pytest.raises(IntegrityError):
        await env.x(
            "UPDATE run_completion SET agent_state='none', human_state='accepted',"
            " decided_by='eng2', decided_at=:n",
            n=now,
        )
    with pytest.raises(IntegrityError):
        await env.x("UPDATE run_completion SET human_state='accepted'")  # nobody decided
    await env.x(
        "UPDATE run_completion SET human_state='accepted', decided_by='eng2', decided_at=:n", n=now
    )


async def test_a_run_whose_completion_was_accepted_is_read_only(env):
    await env.new_run("r1")
    sub = await env.sub("r1", "a")
    await env.post("lead", f"{R}/subtasks/{sub['id']}/evidence", {"kind": "note", "ref": "n"})
    await env.post("lead", f"{R}/subtasks/{sub['id']}/complete", {"expectedVersion": 1})
    digest = (await env.report("r1", "lead"))["agentReported"]["digest"]
    await env.post("eng2", f"{R}/completion/accept", {"reportDigest": digest})
    for method, path, body in (
        ("POST", f"{R}/subtasks", {"title": "late"}),
        ("PUT", f"{R}/execution-limits", {"maxSeconds": 5}),
        ("POST", f"{R}/completion/report", {"summary": "again"}),
        ("POST", f"{R}/subtasks/{sub['id']}/evidence", {"kind": "note", "ref": "m"}),
    ):
        err(await env.call("lead", method, path, json=body), 409, "COMPLETION_ACCEPTED")
    err(
        await env.post("lead", f"{R}/abandon", {"reason": "changed my mind"}),
        409,
        "COMPLETION_ACCEPTED",
    )
    assert (await env.get("lead", f"{R}/subtasks")).json()["summary"]["total"] == 1


async def test_race_accept_and_reject_cannot_both_win(env):
    await env.new_run("r1")
    digest = (await env.report("r1", "lead"))["agentReported"]["digest"]
    res = await asyncio.gather(
        env.post("eng2", f"{R}/completion/accept", {"reportDigest": digest}),
        env.post(
            "boss", f"{R}/completion/reject", {"reportDigest": digest, "reason": "not good enough"}
        ),
    )
    assert sorted(r.status_code for r in res) == [200, 409]
    hist = await env.q("SELECT event FROM run_completion_history WHERE event <> 'reported'")
    assert len(hist) == 1
    final = (await env.q("SELECT human_state FROM run_completion"))[0].human_state
    assert final == hist[0].event


async def test_race_two_reports_keep_numbers_and_history_consistent(env):
    await env.new_run("r1")
    res = await asyncio.gather(
        env.post("lead", f"{R}/completion/report", {"summary": "a"}),
        env.post("eng2", f"{R}/completion/report", {"summary": "b"}),
    )
    won = [r for r in res if r.status_code == 201]
    assert won and all(r.status_code in (201, 409) for r in res)
    row = (await env.q("SELECT report_no FROM run_completion"))[0]
    assert row.report_no == len(won) == len(await env.q("SELECT 1 FROM run_completion_history"))


async def test_race_same_request_id_report_is_one_report(env):
    await env.new_run("r1")
    res = await asyncio.gather(
        *[
            env.post("lead", f"{R}/completion/report", {"summary": "s", "requestId": "rep-1"})
            for _ in range(4)
        ]
    )
    assert sorted(r.status_code for r in res).count(201) == 1
    assert all(r.status_code in (200, 201) for r in res)
    assert (await env.q("SELECT report_no FROM run_completion"))[0].report_no == 1
    assert len(await env.audit("completion.reported")) == 1


async def test_report_validation_and_audit_never_carry_the_text(env):
    await env.new_run("r1")
    for bad in (
        {"summary": ""},
        {"summary": "x" * 2001},
        {"summary": "s", "outputs": [{"kind": "x", "ref": "r"}]},
        {"summary": "s", "outputs": [{"kind": "file", "ref": "../../etc/passwd"}]},
        {"summary": "s", "outputs": [{"kind": "diff", "ref": "d"}] * 51},
    ):
        assert (await env.post("lead", f"{R}/completion/report", bad)).status_code == 422, bad
    await env.report(
        "r1", "lead", "SECRET SUMMARY", outputs=[{"kind": "diff", "ref": "SECRET-REF"}]
    )
    blob = " ".join(r.detail for r in await env.audit())
    assert "SECRET" not in blob
    assert {r.action for r in await env.audit() if r.action.startswith("completion.")} == {
        "completion.reported"
    }
