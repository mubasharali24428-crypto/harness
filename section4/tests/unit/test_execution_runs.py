"""Parts 44.005/44.006/44.009/44.010 (lane EX): execution limits at run start, queue claim and the
running sweep; the checkpoint and safe-resume answer; abandon or fail with preserved evidence; retry
that cannot duplicate side effects. Sqlite with the REAL migrations 0041 and 0048, a real
OrchestratorService with a stub engine and a bus that persists events, a fake clock. Behaviour:
docs/execution.md."""

import asyncio
import json
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from harness_orchestrator import execution
from harness_orchestrator.execution import (
    ExecutionBlockedError,
    check_execution_start,
    executing_seconds,
    execution_block,
    sweep_running_limits,
)
from harness_orchestrator.state_machine import AgentState

from .test_execution_support import MIGRATIONS, RESERVED, T0, err, statements
from .test_execution_support import env as env  # the shared fixture

R = "/runs/r1"
RETRY = R + "/retry"


async def limits(env, **body):
    r = await env.call("lead", "PUT", f"{R}/execution-limits", json={"expectedVersion": 0, **body})
    assert r.status_code == 200, r.text


async def status(env, rid="r1", user="lead"):
    r = await env.get(user, f"/runs/{rid}/execution-status")
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------- the seconds arithmetic


def at(**kw):
    return T0 + timedelta(**kw)


def test_executing_seconds_counts_closed_intervals_and_only_a_running_claim():
    ev = [
        ("execution_started", at()),
        ("paused", at(seconds=100)),
        ("resumed", at(seconds=500)),
        ("engine_failed", at(seconds=530)),
        ("resumed", at(seconds=900)),
    ]
    now = at(seconds=1000)
    assert executing_seconds(ev, now, ("running", at(seconds=900)), True) == 130 + 100
    # claimed late: the wait in the queue is not burned
    assert executing_seconds(ev, now, ("running", at(seconds=980)), True) == 130 + 20
    assert executing_seconds(ev, now, ("queued", at(seconds=900)), True) == 130
    assert executing_seconds(ev, now, None, True) == 130  # no claim: nothing is running
    assert (
        executing_seconds(ev, now, None, False) == 130 + 100
    )  # no queue table: open interval counts
    assert executing_seconds([], now, None, True) == 0
    # a stop without a start and a double start do not go negative or double count
    assert executing_seconds([("paused", at())], now, None, True) == 0
    dbl = [("execution_started", at()), ("resumed", at(seconds=50)), ("paused", at(seconds=100))]
    assert executing_seconds(dbl, now, None, True) == 100


# ------------------------------------------------------------ start, claim, sweep


async def test_start_is_refused_when_the_model_call_budget_is_used(env):
    await env.new_run("r1")
    await limits(env, maxModelCalls=2)
    await env.ledger("r1", "0.01", n=2)
    with pytest.raises(ExecutionBlockedError) as ei:
        await env.svc.start_execution("r1")
    assert ei.value.status_code == 409
    assert ei.value.detail["error"]["code"] == "EXECUTION_LIMIT_EXCEEDED"
    assert ei.value.detail["exceeded"] == [{"dimension": "modelCalls", "limit": 2, "used": 2}]
    assert await env.q("SELECT 1 FROM run_claims") == []  # never enqueued
    (row,) = await env.audit("execution.limit_exceeded")
    detail = json.loads(row.detail)
    assert detail["where"] == "start" and detail["runId"] == "r1" and detail["teamId"] == "team-a"
    assert env.engine.calls == []
    await limits_raise(env, maxModelCalls=3)
    await env.svc.start_execution("r1")
    assert len(await env.q("SELECT 1 FROM run_claims")) == 1


async def limits_raise(env, **body):
    r = await env.call("lead", "PUT", f"{R}/execution-limits", json={"expectedVersion": 1, **body})
    assert r.status_code == 200, r.text


async def test_start_is_refused_when_the_cost_budget_is_used(env):
    await env.new_run("r1")
    await limits(env, maxCostMicrousd=400_000)
    await env.ledger("r1", "0.25", n=1)
    await env.start("r1")  # 250000 < 400000; start() waits for the event publishes
    await env.x("DELETE FROM run_claims")
    await env.ledger("r1", "0.15")  # now exactly 400000
    env.svc.runs["r1"]["state_machine"].state = AgentState.AWAITING_PLAN_APPROVAL
    env.svc.runs["r1"]["state_machine"].plan_approved = True
    await env.x("DELETE FROM agent_events")
    with pytest.raises(ExecutionBlockedError) as ei:
        await env.svc.start_execution("r1")
    assert ei.value.detail["exceeded"] == [
        {"dimension": "costMicrousd", "limit": 400_000, "used": 400_000}
    ]


async def test_tool_call_limit_is_enforced_from_durable_tool_records(env):
    await env.new_run("r1")
    await limits(env, maxToolCalls=1)
    await env.x("""CREATE TABLE agent_tool_calls (
        id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL, run_id TEXT NOT NULL,
        team_id TEXT NOT NULL, tool TEXT NOT NULL, summary TEXT NOT NULL,
        outcome TEXT NOT NULL, cost_microusd INTEGER NOT NULL DEFAULT 0,
        reported_by TEXT NOT NULL, created_at TEXT NOT NULL)""")
    await env.x("""INSERT INTO agent_tool_calls
        (id,assignment_id,run_id,team_id,tool,summary,outcome,reported_by,created_at)
        VALUES ('c1','a1','r1','team-a','read_file','read','ok','lead','2026-01-01')""")
    with pytest.raises(ExecutionBlockedError) as ei:
        await env.start("r1")
    s = await status(env)
    tool = s["enforcement"]["toolCalls"]
    assert tool["enforced"] is True and tool["limit"] == 1 and tool["used"] == 1
    assert tool["exceeded"] is True and s["usage"]["toolCalls"] == 1
    assert ei.value.detail["exceeded"] == [
        {"dimension": "toolCalls", "limit": 1, "used": 1}
    ]
    assert env.engine.calls == []


async def test_queued_run_that_used_its_time_is_blocked_at_claim_and_never_runs(env):
    await env.new_run("r1")
    await limits(env, maxSeconds=3600)
    await env.start("r1")  # execution_started is in the durable log, the claim is queued
    env.tick(hours=2)
    assert await env.svc.poll_once() is None
    assert env.engine.calls == []  # the engine never saw it
    assert await env.q("SELECT 1 FROM run_claims") == []  # the claim was finished, not left to spin
    assert env.seat("r1") == "blocked"  # state machine vocabulary: EXECUTING -> BLOCKED
    await env.flush()
    failed = await env.events("r1", "engine_failed")
    assert "execution limit reached: seconds" in json.loads(failed[-1].payload)["reason"]
    (row,) = await env.audit("execution.limit_exceeded")
    assert json.loads(row.detail)["where"] == "claim"
    s = await status(env)
    assert s["state"] == "blocked" and s["enforcement"]["seconds"]["exceeded"] is True
    assert s["failure"]["reason"].startswith("execution limit reached")
    assert [r["code"] for r in s["resume"]["reasons"]] == ["EXECUTION_LIMIT_EXCEEDED"]
    # raising the limit makes the run resumable, and the claim gate then lets it through
    await limits_raise(env, maxSeconds=100_000)
    r = await env.post("lead", RETRY, {"requestId": "retry-after-limit"})
    assert r.status_code == 202, r.text
    assert await env.svc.poll_once() == "r1"
    await env.svc.runs["r1"]["task"]
    assert env.engine.calls == ["r1"]


async def test_sweep_pauses_a_running_run_that_reached_its_limit_and_only_once(env):
    await env.new_run("r1")
    await env.new_run("r2")
    await limits(env, maxSeconds=3600)
    await env.start("r1")
    await env.start("r2")  # r2 has no limits at all
    await env.x("UPDATE run_claims SET state = 'running', worker_id = 'other-worker'")
    assert await sweep_running_limits(env.svc) == []  # not yet over
    env.tick(hours=2)
    assert await sweep_running_limits(env.svc) == ["r1"]
    assert env.seat("r1") == "paused" and env.seat("r2") == "executing"
    assert await sweep_running_limits(env.svc) == []  # the pause released the claim
    assert [r.run_id for r in await env.q("SELECT run_id FROM run_claims")] == ["r2"]
    (row,) = await env.audit("execution.limit_exceeded")
    detail = json.loads(row.detail)
    assert detail["where"] == "running" and detail["action"] == "paused"
    assert detail["exceeded"][0]["dimension"] == "seconds"


async def test_the_gate_allows_runs_without_limits_and_old_schemas_and_fails_closed_on_errors(env):
    await env.new_run("r1")
    assert await execution_block(env.factory, "r1") is None
    assert (
        await execution_block(env.factory, None) is None
        and await execution_block(None, "r1") is None
    )

    class Broken:
        def __call__(self):
            return self

        async def __aenter__(self):
            raise RuntimeError("connection reset by peer")

        async def __aexit__(self, *a):
            return False

    with pytest.raises(RuntimeError):
        await execution_block(Broken(), "r1")
    await env.x("DROP TABLE run_attempts")
    await env.x("DROP TABLE run_execution_limits")
    assert await execution_block(env.factory, "r1") is None  # pre-0048 schema: nothing to enforce


async def test_an_abandoned_run_is_blocked_by_the_gate_and_the_refusal_is_audited(env):
    await env.new_run("r1")
    assert (await env.post("lead", f"{R}/abandon", {"reason": "wrong approach"})).status_code == 200
    block = await execution_block(env.factory, "r1")
    assert block["code"] == "RUN_ABANDONED"
    assert (await check_execution_start(env.svc, "r1", where="claim"))["code"] == "RUN_ABANDONED"
    (row,) = await env.audit("run.retry_refused")
    assert json.loads(row.detail)["code"] == "RUN_ABANDONED"
    with pytest.raises(ExecutionBlockedError) as ei:
        await execution.enforce_execution_start(env.svc, "r1")
    assert ei.value.detail["error"]["code"] == "RUN_ABANDONED"


# ---------------------------------------------------- checkpoints and safe resume


async def test_status_of_a_fresh_run_is_complete_and_says_resume_is_not_possible(env):
    await env.new_run("r1")
    s = await status(env, user="view")
    assert s["state"] == "awaiting_plan_approval" and s["usage"]["seconds"] == 0
    assert s["limits"]["isDefault"] is True and s["subtasks"]["total"] == 0
    assert s["completion"] == {"agentState": "none", "humanState": "none", "reportNo": 0}
    assert s["resume"]["safe"] is False
    assert [r["code"] for r in s["resume"]["reasons"]] == ["RUN_NOT_RESUMABLE"]
    assert set(s["enforcement"]) == {"seconds", "modelCalls", "toolCalls", "costMicrousd"}
    assert s["checkpoints"]["workspace"]["via"].endswith("/proj-r1/checkpoints")
    assert s["attempts"] == [] and s["failure"] is None


async def test_a_failed_run_shows_its_checkpoints_failure_and_a_safe_resume_point(env):
    await env.blocked_run("r1")
    s = await status(env)
    types = [e["type"] for e in s["checkpoints"]["events"]]
    assert types == ["execution_started", "engine_failed"]
    assert s["state"] == "blocked" and s["usage"]["seconds"] == 5
    assert s["failure"]["reason"].startswith("boom") and len(s["failure"]["reason"]) <= 200
    res = s["resume"]
    assert res["safe"] is True and res["reasons"] == [] and res["nextAttemptNo"] == 2
    assert (
        res["resumeFrom"]["kind"] == "engine_session_history"
        and res["resumeFrom"]["sessionId"] == "r1"
    )
    assert res["resumeFrom"]["lastEventType"] == "engine_failed"
    assert "not rolled back" in res["caveat"]
    # W3: the caveat must not point at workspace checkpoints — run workspaces are
    # not snapshotted, so telling an operator to review them named a control that
    # does not exist. Pin the truthful text, not just its first clause.
    assert "checkpoints" not in res["caveat"]
    assert "on disk" in res["caveat"]


async def test_unreconciled_side_effects_make_resume_unsafe(env):
    await env.blocked_run("r1")
    for state, safe in (
        ("pending", True),
        ("completed", True),
        ("rejected", True),
        ("approved", False),
        ("executing", False),
    ):
        await env.x("DELETE FROM high_impact_approvals")
        await env.approval("r1", state)
        res = (await status(env))["resume"]
        assert res["safe"] is safe, state
        if not safe:
            (reason,) = res["reasons"]
            assert reason["code"] == "UNRECONCILED_SIDE_EFFECTS"
            assert reason["approvals"] == [{"id": "ap1", "state": state}]  # ids and states only


async def test_a_live_claim_or_task_or_pending_retry_means_the_previous_attempt_still_holds_the_run(
    env,
):
    await env.blocked_run("r1")
    await env.x(
        "INSERT INTO run_claims (run_id, project_id, state, claimed_at, heartbeat_at, worker_id)"
        " VALUES ('r1', 'proj-r1', 'queued', :t, :t, 'w')",
        t=env.clock["t"].isoformat(),
    )
    assert [r["code"] for r in (await status(env))["resume"]["reasons"]] == ["ATTEMPT_IN_PROGRESS"]
    await env.x("DELETE FROM run_claims")
    assert (await status(env))["resume"]["safe"] is True
    env.svc.runs["r1"]["task"] = asyncio.create_task(asyncio.sleep(30))
    assert [r["code"] for r in (await status(env))["resume"]["reasons"]] == ["ATTEMPT_IN_PROGRESS"]
    env.svc.runs["r1"]["task"].cancel()
    await asyncio.gather(env.svc.runs["r1"]["task"], return_exceptions=True)
    assert (await status(env))["resume"]["safe"] is True
    await env.x(
        "INSERT INTO run_attempts (id, run_id, team_id, attempt_no, kind, state, requested_by,"
        " requested_at) VALUES ('a', 'r1', 'team-a', 2, 'retry', 'requested', 'lead', :t)",
        t=env.clock["t"],
    )
    res = (await status(env))["resume"]
    assert res["reasons"] == [{"code": "ATTEMPT_IN_PROGRESS", "attemptNo": 2}]
    env.tick(seconds=execution.STALE_REQUEST_SECONDS)
    assert (await status(env))["resume"][
        "safe"
    ] is True  # a request that never resolved frees the run


async def test_paused_session_and_archived_workspace_make_resume_unsafe(env):
    await env.blocked_run("r1")
    await env.x(
        "INSERT INTO collab_sessions (id, team_id, title, owner_user_id, state, created_at,"
        " updated_at) VALUES ('s1', 'team-a', 't', 'lead', 'paused', :t, :t)",
        t=env.clock["t"],
    )
    await env.x(
        "INSERT INTO session_runs (run_id, session_id, linked_by, linked_at)"
        " VALUES ('r1', 's1', 'lead', :t)",
        t=env.clock["t"],
    )
    await env.x("INSERT INTO workspaces (id, state) VALUES ('w1', 'archived')")
    await env.x("UPDATE projects SET workspace_id = 'w1'")
    codes = [r["code"] for r in (await status(env))["resume"]["reasons"]]
    assert codes == ["SESSION_NOT_ACTIVE", "WORKSPACE_NOT_ACTIVE"]


async def test_status_is_a_404_for_a_foreign_team_and_readable_by_a_viewer(env):
    await env.blocked_run("r1")
    assert (await env.get("evil", f"{R}/execution-status")).status_code == 404
    assert (await env.get("guest", f"{R}/execution-status")).status_code == 200


# ----------------------------------------------------------------------- abandon


async def test_abandon_stops_the_run_records_the_reason_and_deletes_nothing(env):
    await env.blocked_run("r1")
    sub = await env.sub("r1", "keep me")
    await env.post("lead", f"{R}/subtasks/{sub['id']}/evidence", {"kind": "diff", "ref": "d-1"})
    await env.ledger("r1", "0.02", n=2)
    await env.report("r1", "lead", "partial work")
    r = await env.post(
        "eng2", f"{R}/abandon", {"reason": "approach was wrong", "requestId": "ab-1"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["abandoned"] is True and body["duplicate"] is False
    assert (
        body["attempt"]["state"] == "abandoned"
        and body["attempt"]["reason"] == "approach was wrong"
    )
    assert body["preserved"] == {
        "subtasks": 1,
        "evidenceRefs": 1,
        "openBlockers": 0,
        "ledgerRows": 2,
        "lastEventSeq": body["preserved"]["lastEventSeq"],
        "checkpointEvents": body["preserved"]["checkpointEvents"],
        "completion": {"agentState": "reported", "humanState": "none"},
        "deleted": "nothing",
    }
    assert (
        env.svc.runs.get("r1") is None or env.seat("r1") == "done"
    )  # DONE: the state machine's word
    await env.flush()
    assert await env.events("r1", "paused")  # stop's tombstone event
    # every record is still there and readable
    assert (await env.get("view", f"{R}/subtasks")).json()["subtasks"][0]["evidenceCount"] == 1
    assert (await env.get("view", f"{R}/completion")).json()["agentReported"][
        "summary"
    ] == "partial work"
    assert len(await env.q("SELECT 1 FROM cost_ledger")) == 2
    assert len(await env.q("SELECT 1 FROM agent_events WHERE run_id = 'r1'")) >= 3
    (row,) = await env.audit("run.abandoned")
    detail = json.loads(row.detail)
    assert detail["before"] == {"state": "blocked"} and detail["after"] == {"state": "done"}
    assert detail["outcome"] == "abandoned" and detail["userId"] == "eng2"
    assert (
        "approach was wrong" not in row.detail
    )  # the reason lives in the record, not in audit text


async def test_abandon_requires_a_reason_a_role_and_ownership(env):
    await env.new_run("r1")
    await env.new_run("rb", "team-b")
    for body in ({}, {"reason": ""}, {"reason": "xx"}, {"reason": "y" * 501}):
        assert (await env.post("lead", f"{R}/abandon", body)).status_code == 422, body
    err(await env.post("view", f"{R}/abandon", {"reason": "not allowed"}), 403, "ROLE_DENIED")
    assert (await env.post("lead", "/runs/rb/abandon", {"reason": "not mine"})).status_code == 404
    assert await env.q("SELECT 1 FROM run_attempts") == []
    assert env.seat("r1") != "done"


async def test_abandon_can_record_a_failure_and_a_repeat_changes_nothing(env):
    await env.blocked_run("r1")
    first = await env.post(
        "lead", f"{R}/abandon", {"reason": "cannot be fixed", "outcome": "failed"}
    )
    assert first.json()["attempt"]["state"] == "failed"
    again = await env.post("eng2", f"{R}/abandon", {"reason": "a different reason"})
    assert (
        again.json()["duplicate"] is True and again.json()["attempt"]["reason"] == "cannot be fixed"
    )
    assert len(await env.q("SELECT 1 FROM run_attempts WHERE kind = 'abandon'")) == 1
    assert len(await env.audit("run.abandoned")) == 1


async def test_abandon_refuses_a_run_that_already_finished(env):
    await env.new_run("r1")
    await env.svc.stop_agent("r1")
    await env.flush()
    err(
        await env.post("lead", f"{R}/abandon", {"reason": "too late to matter"}),
        409,
        "RUN_ALREADY_FINISHED",
    )
    assert await env.q("SELECT 1 FROM run_attempts") == []


async def test_an_abandoned_run_keeps_its_records_read_only_and_cannot_be_retried(env):
    await env.blocked_run("r1")
    sub = await env.sub("r1", "a")
    await env.post("lead", f"{R}/abandon", {"reason": "giving up"})
    for method, path, body in (
        ("POST", f"{R}/subtasks", {"title": "late"}),
        ("POST", f"{R}/subtasks/{sub['id']}/evidence", {"kind": "note", "ref": "n"}),
        ("PUT", f"{R}/execution-limits", {"maxSeconds": 9}),
        ("POST", f"{R}/completion/report", {"summary": "s"}),
    ):
        err(await env.call("lead", method, path, json=body), 409, "RUN_ABANDONED")
    r = await env.post("lead", RETRY, {"requestId": "retry-after-abandon"})
    detail = err(r, 409, "RUN_ABANDONED")
    assert "RUN_ABANDONED" in [x["code"] for x in detail["reasons"]]
    assert await env.audit("run.retry_refused")
    assert (await env.get("lead", f"{R}/subtasks")).status_code == 200
    assert env.engine.calls == ["r1"]  # nothing ran again


async def test_race_two_abandons_leave_one_record_and_one_audit_row(env):
    await env.blocked_run("r1")
    res = await asyncio.gather(
        *[env.post(u, f"{R}/abandon", {"reason": f"reason {u}"}) for u in ("lead", "eng2", "boss")]
    )
    assert all(r.status_code == 200 for r in res), [r.text for r in res]
    assert sorted(r.json()["duplicate"] for r in res) == [False, True, True]
    assert len({r.json()["attempt"]["id"] for r in res}) == 1
    assert len(await env.q("SELECT 1 FROM run_attempts WHERE kind = 'abandon'")) == 1
    assert len(await env.audit("run.abandoned")) == 1


# ------------------------------------------------------------------------- retry


async def test_retry_resumes_a_failed_run_once_with_an_attempt_number_and_audit(env):
    await env.blocked_run("r1")
    env.tick(seconds=30)
    r = await env.post("lead", RETRY, {"requestId": "retry-0001", "reason": "provider was down"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["duplicate"] is False and body["attempt"]["attemptNo"] == 2
    assert (
        body["attempt"]["state"] == "resumed" and body["attempt"]["reason"] == "provider was down"
    )
    assert env.seat("r1") == "executing"
    assert [c.state for c in await env.q("SELECT state FROM run_claims")] == ["queued"]
    row = (await env.q("SELECT from_state, from_event_type, from_event_seq FROM run_attempts"))[0]
    assert (row.from_state, row.from_event_type) == (
        "blocked",
        "engine_failed",
    ) and row.from_event_seq
    await env.work("r1")  # the worker claims it and the engine is called with the SAME session id
    assert env.engine.calls == ["r1", "r1"]
    actions = [a.action for a in await env.audit() if a.action.startswith(("run.", "execution."))]
    assert actions == ["run.retry_requested", "execution.checkpoint_resumed"]
    assert set(actions) <= RESERVED
    resumed = json.loads((await env.audit("execution.checkpoint_resumed"))[0].detail)
    assert resumed["attemptNo"] == 2 and resumed["fromEventType"] == "engine_failed"
    s = await status(env)
    assert [(a["attemptNo"], a["state"]) for a in s["attempts"]] == [(2, "resumed")]


async def test_retry_needs_a_request_id_and_a_write_role_and_ownership(env):
    await env.blocked_run("r1")
    await env.new_run("rb", "team-b")
    for body in ({}, {"requestId": "short"}, {"requestId": "x" * 65}):
        assert (await env.post("lead", RETRY, body)).status_code == 422
    err(await env.post("view", RETRY, {"requestId": "retry-view-1"}), 403, "ROLE_DENIED")
    assert (
        await env.post("lead", "/runs/rb/retry", {"requestId": "retry-evil-1"})
    ).status_code == 404
    assert env.seat("r1") == "blocked" and await env.q("SELECT 1 FROM run_attempts") == []


async def test_a_repeated_request_id_is_the_same_attempt_and_resumes_nothing_twice(env):
    await env.blocked_run("r1")
    first = await env.post("lead", RETRY, {"requestId": "retry-dup-1"})
    await env.work("r1")
    again = await env.post("lead", RETRY, {"requestId": "retry-dup-1"})
    assert first.status_code == 202 and again.status_code == 200
    assert again.json()["duplicate"] is True
    assert again.json()["attempt"]["id"] == first.json()["attempt"]["id"]
    assert env.engine.calls == ["r1", "r1"]  # one original run and one retry, not three
    assert len(await env.q("SELECT 1 FROM run_attempts")) == 1
    assert len(await env.audit("run.retry_requested")) == 1


async def test_retry_is_refused_with_stable_codes_and_each_refusal_is_audited(env):
    await env.blocked_run("r1")
    await env.approval("r1", "executing")
    r = await env.post("lead", RETRY, {"requestId": "retry-side-1"})
    detail = err(r, 409, "UNRECONCILED_SIDE_EFFECTS")
    assert detail["reasons"][0]["approvals"] == [{"id": "ap1", "state": "executing"}]
    await env.x("DELETE FROM high_impact_approvals")
    await limits(env, maxModelCalls=1)
    await env.ledger("r1", "0.01")
    err(
        await env.post("lead", RETRY, {"requestId": "retry-lim-001"}),
        409,
        "EXECUTION_LIMIT_EXCEEDED",
    )
    await limits_raise(env, maxModelCalls=5)
    ok = await env.post("lead", RETRY, {"requestId": "retry-ok-0001"})
    assert ok.status_code == 202
    again = await env.post("lead", RETRY, {"requestId": "retry-two-001"})
    err(again, 409, "RUN_NOT_RESUMABLE")  # the first retry holds the run
    refused = await env.audit("run.retry_refused")
    codes = [json.loads(a.detail)["codes"] for a in refused]
    assert codes == [
        ["UNRECONCILED_SIDE_EFFECTS"],
        ["EXECUTION_LIMIT_EXCEEDED"],
        ["RUN_NOT_RESUMABLE", "ATTEMPT_IN_PROGRESS"],
    ]
    assert len(await env.q("SELECT 1 FROM run_attempts")) == 1
    assert env.seat("r1") == "executing"


async def test_retry_is_refused_for_a_run_that_is_not_failed_or_paused(env):
    await env.new_run("r1")  # awaiting plan approval flow: never started
    err(await env.post("lead", RETRY, {"requestId": "retry-idle-01"}), 409, "RUN_NOT_RESUMABLE")
    await env.start("r1")
    err(await env.post("lead", RETRY, {"requestId": "retry-exec-01"}), 409, "RUN_NOT_RESUMABLE")
    assert env.engine.calls == [] and await env.q("SELECT 1 FROM run_attempts") == []


async def test_attempt_numbers_count_up_across_failures(env):
    await env.blocked_run("r1")
    for n in (2, 3, 4):
        r = await env.post("lead", RETRY, {"requestId": f"retry-n-{n}000"})
        assert r.status_code == 202 and r.json()["attempt"]["attemptNo"] == n
        await env.work("r1", fail=True)
        assert env.seat("r1") == "blocked"
    assert env.engine.calls == ["r1"] * 4
    s = await status(env)
    assert [a["attemptNo"] for a in s["attempts"]] == [2, 3, 4] and s["resume"][
        "nextAttemptNo"
    ] == 5


async def test_a_retry_that_cannot_resume_is_recorded_and_surfaced(env, monkeypatch):
    await env.blocked_run("r1")

    async def boom(run_id):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(env.svc, "resume_agent", boom)
    r = await env.post("lead", RETRY, {"requestId": "retry-boom-01"})
    err(r, 409, "RESUME_FAILED")
    row = (await env.q("SELECT state, error_code FROM run_attempts"))[0]
    assert (row.state, row.error_code) == ("start_failed", "RESUME_FAILED")
    assert env.seat("r1") == "blocked" and await env.audit("execution.checkpoint_resumed") == []
    replay = await env.post("lead", RETRY, {"requestId": "retry-boom-01"})
    assert replay.status_code == 200 and replay.json()["errorCode"] == "RESUME_FAILED"

    async def noop(run_id):
        return None

    monkeypatch.setattr(env.svc, "resume_agent", noop)
    err(await env.post("lead", RETRY, {"requestId": "retry-noop-01"}), 409, "RESUME_NOT_APPLIED")
    codes = [
        x.error_code for x in await env.q("SELECT error_code FROM run_attempts ORDER BY attempt_no")
    ]
    assert codes == [
        "RESUME_FAILED",
        "RESUME_NOT_APPLIED",
    ]  # a failed start never blocks the next try


async def test_a_retry_stuck_in_requested_frees_the_run_after_the_stale_window(env):
    await env.blocked_run("r1")
    await env.x(
        "INSERT INTO run_attempts (id, run_id, team_id, attempt_no, kind, state, requested_by,"
        " requested_at) VALUES ('old', 'r1', 'team-a', 2, 'retry', 'requested', 'eng2', :t)",
        t=env.clock["t"],
    )
    env.tick(seconds=10)
    detail = err(
        await env.post("lead", RETRY, {"requestId": "retry-held-01"}), 409, "ATTEMPT_IN_PROGRESS"
    )
    assert detail["reasons"][0]["attemptNo"] == 2
    env.tick(seconds=execution.STALE_REQUEST_SECONDS)
    ok = await env.post("lead", RETRY, {"requestId": "retry-free-01"})
    assert ok.status_code == 202 and ok.json()["attempt"]["attemptNo"] == 3
    old = (await env.q("SELECT state, error_code FROM run_attempts WHERE id = 'old'"))[0]
    assert (old.state, old.error_code) == ("start_failed", "STALE_REQUEST")


async def test_race_same_request_id_retries_resume_the_run_exactly_once(env):
    await env.blocked_run("r1")
    res = await asyncio.gather(
        *[env.post("lead", RETRY, {"requestId": "retry-race-1"}) for _ in range(6)]
    )
    assert sorted(r.status_code for r in res) == [200] * 5 + [202]
    assert len({r.json()["attempt"]["id"] for r in res}) == 1
    assert len(await env.q("SELECT 1 FROM run_attempts")) == 1
    assert len(await env.q("SELECT 1 FROM run_claims")) == 1
    assert len(await env.audit("run.retry_requested")) == 1
    await env.flush()
    assert (
        len(await env.events("r1", "resumed")) == 1
    )  # one RESUMED envelope, one engine run to come
    await env.work("r1")
    assert env.engine.calls == ["r1", "r1"]


async def test_race_different_request_ids_only_one_retry_wins(env):
    await env.blocked_run("r1")
    res = await asyncio.gather(
        *[
            env.post(u, RETRY, {"requestId": f"retry-diff-{i}"})
            for i, u in enumerate(("lead", "eng2", "boss", "lead"))
        ]
    )
    codes = sorted(r.status_code for r in res)
    assert codes == [202, 409, 409, 409], [r.text for r in res]
    assert len(await env.q("SELECT 1 FROM run_attempts")) == 1
    assert len(await env.q("SELECT 1 FROM run_claims")) == 1
    await env.flush()
    assert len(await env.events("r1", "resumed")) == 1
    await env.work("r1")
    assert env.engine.calls == ["r1", "r1"]


async def test_a_retry_that_loses_to_another_process_after_looking_leaves_no_attempt_row(
    env, monkeypatch
):
    await env.blocked_run("r1")
    real, seen = execution.assess_resume, []

    async def spy(service, db, run_id, now, **kw):
        out = await real(service, db, run_id, now, **kw)
        seen.append(out["safe"])
        if len(seen) == 1:  # a retry in another process resumed the run right after our first look
            await env.x(
                "INSERT INTO run_claims (run_id, project_id, state, claimed_at, heartbeat_at,"
                " worker_id) VALUES ('r1', 'proj-r1', 'queued', :t, :t, 'other')",
                t=env.clock["t"].isoformat(),
            )
        return out

    monkeypatch.setattr(execution, "assess_resume", spy)
    err(await env.post("lead", RETRY, {"requestId": "retry-late-01"}), 409, "ATTEMPT_IN_PROGRESS")
    assert seen == [True, False]
    assert await env.q("SELECT 1 FROM run_attempts") == []  # the insert was rolled back
    assert env.seat("r1") == "blocked" and len(await env.audit("run.retry_refused")) == 1


# ------------------------------------------------------------- migration invariants


async def test_unique_indexes_hold_the_race_invariants_even_for_buggy_callers(env):
    await env.new_run("r1")
    ins = (
        "INSERT INTO run_attempts (id, run_id, team_id, attempt_no, kind, state, reason, "
        "requested_by,"
        " requested_at) VALUES (:i, 'r1', 'team-a', :n, :k, :s, :re, 'lead', :t)"
    )
    t = env.clock["t"]
    await env.x(ins, i="1", n=2, k="retry", s="requested", re=None, t=t)
    with pytest.raises(IntegrityError):  # a second in-flight retry
        await env.x(ins, i="2", n=3, k="retry", s="requested", re=None, t=t)
    with pytest.raises(IntegrityError):  # the same attempt number twice
        await env.x(ins, i="3", n=2, k="retry", s="start_failed", re=None, t=t)
    await env.x(
        ins, i="4", n=3, k="retry", s="start_failed", re=None, t=t
    )  # finished attempts do not collide
    with pytest.raises(IntegrityError):  # an abandon needs a reason
        await env.x(ins, i="5", n=3, k="abandon", s="abandoned", re=None, t=t)
    await env.x(ins, i="6", n=3, k="abandon", s="abandoned", re="because", t=t)
    with pytest.raises(IntegrityError):  # abandoned at most once
        await env.x(ins, i="7", n=3, k="abandon", s="failed", re="again", t=t)


async def test_the_migration_is_idempotent_and_rejects_bad_values(env):
    async with env.factory() as db:
        for stmt in statements((MIGRATIONS / "0048_execution.sql").read_text()):
            await db.execute(text(stmt))
        await db.commit()
    await env.new_run("r1")
    for sql in (
        "INSERT INTO run_execution_limits (run_id, team_id, max_seconds, updated_by, updated_at)"
        " VALUES ('r1', 'team-a', 0, 'u', :t)",
        "INSERT INTO run_subtasks (id, run_id, team_id, title, state, created_by, created_at,"
        " updated_by, updated_at) VALUES ('x', 'r1', 'team-a', 't', 'bogus', 'u', :t, 'u', :t)",
    ):
        with pytest.raises(IntegrityError):
            await env.x(sql, t=env.clock["t"])
