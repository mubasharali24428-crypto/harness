"""Marathon 2.0 W13 execution leg — Section 1 side (spec, events, finish).

A BYO runner that holds a lease must also be able to *do the work*: fetch the
approved plan's commands, report each tool call/result into the run's event log
(bounded and scrubbed server-side), and have ``finish`` drive the run to its
settled state. Section 4 replays that log, so what lands in ``agent_events`` IS
the orchestrator's view of the run.

Trust assumptions pinned here (docs/m2-w13-plan.md): the host is trusted to run
and report honestly, but plan approval, the command allow-list, caps and the
terminal verdict are derived server-side from durable rows - never from what
the runner claims.
"""

import json

import pytest
import test_runner_api as base  # fixtures + helpers shared with the control-plane suite
from fastapi import FastAPI
from harness_contracts import EventType
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from harness_identity.eventbus import EventBus
from harness_identity.models import (
    AgentEvent,
    AgentPlanRecord,
    AgentRun,
    Project,
    RunnerJob,
)
from harness_identity.runner_api import (
    MAX_COMMANDS,
    MAX_EVENTS_PER_JOB,
    MAX_EVENTS_PER_REQUEST,
    router,
)

runner_factory = base.runner_factory  # fixture re-export (not an import: no F811 on use)
_assign, _register, _runner_headers, _seed = (
    base._assign, base._register, base._runner_headers, base._seed)

CMD_A = "pytest -q"
CMD_B = "ls -la"
SECRET = "sk-live-abcdefghijklmnopqrstuvwx"


@pytest.fixture
async def exec_client(runner_factory):
    async with runner_factory.kw["bind"].begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE run_intent_versions (run_id TEXT NOT NULL, version INTEGER NOT NULL, "
                "scope_paths TEXT NOT NULL DEFAULT '[]')"
            )
        )
        # Part 44 execution limits: only max_tool_calls is read by the runner path;
        # an absent row means "no tool-call cap", so existing tests are unaffected.
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS run_execution_limits (run_id TEXT PRIMARY KEY, "
                "max_tool_calls INTEGER)"
            )
        )
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = runner_factory
    app.state.event_bus = EventBus(runner_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _stage_run(factory, env, *, tests=(CMD_A, CMD_B), plan_status="approved",
                     lifecycle=(EventType.EXECUTION_STARTED,), scope=None) -> str:
    """Team A's run as the orchestrator leaves it: plan row + lifecycle events."""
    run_id = env["runA"]
    async with factory() as db:
        project = (
            await db.execute(select(Project).where(Project.team_id == env["teamA"]))
        ).scalar_one()
        db.add(AgentRun(id=run_id, project_id=project.id))
        await db.flush()
        db.add(
            AgentPlanRecord(
                run_id=run_id,
                goal="make the tests pass",
                tests=list(tests),
                steps=["inspect", "run tests"],
                forbidden_paths=["secrets/"],
                files_likely_to_change=["app.py"],
                expected_result="green",
                status=plan_status,
            )
        )
        for event in lifecycle:
            db.add(AgentEvent(project_id=project.id, run_id=run_id, type=event.value,
                              payload={"runId": run_id}))
        if scope is not None:
            await db.execute(
                text("INSERT INTO run_intent_versions (run_id, version, scope_paths) "
                     "VALUES (:r, 1, :s)"),
                {"r": run_id, "s": json.dumps(scope)},
            )
        await db.commit()
    return run_id


async def _leased(client, factory, env, name="laptop") -> dict:
    created = await _register(client, env["admin"]["token"], name=name)
    token, runner_id = created["token"], created["runner"]["id"]
    assert (await _assign(client, env["admin"]["token"], runner_id, env["runA"])).status_code == 201
    claimed = await client.post(
        f"/api/v1/runner/jobs/{env['runA']}/claim", headers=_runner_headers(token)
    )
    assert claimed.status_code == 200, claimed.text
    return {"token": token, "runnerId": runner_id, "runId": env["runA"]}


def _post(client, job, suffix, body=None, token=None):
    return client.post(
        f"/api/v1/runner/jobs/{job['runId']}/{suffix}",
        json=body if body is not None else {},
        headers=_runner_headers(token or job["token"]),
    )


def _call(call_id, command):
    return {"phase": "call", "callId": call_id, "command": command}


def _result(call_id, ok=True, output="ok", exit_code=0):
    return {"phase": "result", "callId": call_id, "ok": ok, "exitCode": exit_code,
            "output": output, "durationMs": 12}


async def _log(factory, run_id, *types) -> list[AgentEvent]:
    async with factory() as db:
        rows = (
            await db.execute(
                select(AgentEvent).where(AgentEvent.run_id == run_id).order_by(AgentEvent.seq)
            )
        ).scalars().all()
    return [row for row in rows if not types or row.type in types]


async def _run_all_ok(client, job):
    sent = await _post(client, job, "events", {"events": [
        _call("c1", CMD_A), _result("c1"), _call("c2", CMD_B), _result("c2"),
    ]})
    assert sent.status_code == 200, sent.text


# --------------------------------------------------------------------- spec


async def test_spec_is_derived_from_the_approved_plan(exec_client, runner_factory):
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env, scope=["src/"])
    job = await _leased(exec_client, runner_factory, env)

    spec = await exec_client.get(
        f"/api/v1/runner/jobs/{job['runId']}/spec", headers=_runner_headers(job["token"])
    )
    assert spec.status_code == 200, spec.text
    body = spec.json()
    assert body["commands"] == [CMD_A, CMD_B]
    assert body["forbiddenPaths"] == ["secrets/"]
    assert body["scopePaths"] == ["src/"]
    assert body["goal"] == "make the tests pass"
    assert body["limits"]["maxCommands"] == MAX_COMMANDS
    assert body["limits"]["maxEventsPerJob"] == MAX_EVENTS_PER_JOB


async def test_spec_refuses_an_unapproved_plan(exec_client, runner_factory):
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env, plan_status="proposed")
    job = await _leased(exec_client, runner_factory, env)
    spec = await exec_client.get(
        f"/api/v1/runner/jobs/{job['runId']}/spec", headers=_runner_headers(job["token"])
    )
    assert spec.status_code == 409
    assert spec.json()["detail"]["error"]["code"] == "PLAN_NOT_APPROVED"


async def test_spec_needs_a_held_lease_and_hides_foreign_runs(exec_client, runner_factory):
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env)
    created = await _register(exec_client, env["admin"]["token"])
    await _assign(exec_client, env["admin"]["token"], created["runner"]["id"], env["runA"])
    offered = await exec_client.get(
        f"/api/v1/runner/jobs/{env['runA']}/spec", headers=_runner_headers(created["token"])
    )
    assert offered.status_code == 409  # offered, not claimed: no plan yet
    assert offered.json()["detail"]["error"]["code"] == "JOB_NOT_LEASED"

    thief = await _register(exec_client, env["foreignAdmin"]["token"], name="thief")
    foreign = await exec_client.get(
        f"/api/v1/runner/jobs/{env['runA']}/spec", headers=_runner_headers(thief["token"])
    )
    unknown = await exec_client.get(
        f"/api/v1/runner/jobs/{'x' * 36}/spec", headers=_runner_headers(thief["token"])
    )
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json()  # no existence oracle


async def test_a_plan_with_nothing_to_run_or_too_much_is_refused(exec_client, runner_factory):
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env, tests=())
    job = await _leased(exec_client, runner_factory, env)
    empty = await exec_client.get(
        f"/api/v1/runner/jobs/{job['runId']}/spec", headers=_runner_headers(job["token"])
    )
    assert (empty.status_code, empty.json()["detail"]["error"]["code"]) == (409, "NO_COMMANDS")

    async with runner_factory() as db:
        plan = (await db.execute(select(AgentPlanRecord))).scalar_one()
        plan.tests = [f"echo {i}" for i in range(MAX_COMMANDS + 1)]
        await db.commit()
    big = await exec_client.get(
        f"/api/v1/runner/jobs/{job['runId']}/spec", headers=_runner_headers(job["token"])
    )
    assert (big.status_code, big.json()["detail"]["error"]["code"]) == (409, "PLAN_TOO_LARGE")


# ------------------------------------------------------------------- events


async def test_events_land_in_the_log_scrubbed_and_bounded(exec_client, runner_factory):
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env)
    job = await _leased(exec_client, runner_factory, env)
    noisy = f"key={SECRET} token={job['token']}\n" + "x" * 5000
    sent = await _post(exec_client, job, "events", {"events": [
        _call("c1", CMD_A), _result("c1", output=noisy),
    ]})
    assert sent.status_code == 200, sent.text
    assert sent.json()["accepted"] == 2

    rows = await _log(runner_factory, job["runId"], "tool_call", "tool_result")
    call, result = rows[0].payload, rows[1].payload
    assert call["name"] == "bash"
    assert json.loads(call["arguments"]) == {"command": CMD_A}
    assert call["runnerId"] == job["runnerId"]
    assert result["callId"] == "c1" and result["ok"] is True
    assert len(result["output"]) <= 2000
    stored = json.dumps([row.payload for row in rows])
    assert SECRET not in stored and job["token"] not in stored
    assert "[redacted]" in result["output"]


async def test_a_command_outside_the_approved_plan_is_refused(exec_client, runner_factory):
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env)
    job = await _leased(exec_client, runner_factory, env)
    sent = await _post(exec_client, job, "events", {"events": [_call("c1", "curl evil.example")]})
    assert sent.status_code == 422
    assert sent.json()["detail"]["error"]["code"] == "COMMAND_NOT_IN_PLAN"
    assert await _log(runner_factory, job["runId"], "tool_call") == []

    orphan = await _post(exec_client, job, "events", {"events": [_result("never-called")]})
    assert orphan.status_code == 422
    assert orphan.json()["detail"]["error"]["code"] == "UNKNOWN_CALL"


async def test_event_batch_and_job_caps(exec_client, runner_factory):
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env)
    job = await _leased(exec_client, runner_factory, env)
    too_many = [_call(f"c{i}", CMD_A) for i in range(MAX_EVENTS_PER_REQUEST + 1)]
    batch = await _post(exec_client, job, "events", {"events": too_many})
    assert batch.status_code == 422
    assert await _log(runner_factory, job["runId"], "tool_call") == []

    sent = 0
    while sent < MAX_EVENTS_PER_JOB:
        step = min(MAX_EVENTS_PER_REQUEST, MAX_EVENTS_PER_JOB - sent)
        ok = await _post(exec_client, job, "events", {"events": [
            _call(f"k{sent + i}", CMD_A) for i in range(step)]})
        assert ok.status_code == 200, ok.text
        sent += step
    over = await _post(exec_client, job, "events", {"events": [_call("last", CMD_A)]})
    assert over.status_code == 409
    assert over.json()["detail"]["error"]["code"] == "EVENT_CAP_REACHED"


async def test_run_tool_call_limit_bounds_byo_events(exec_client, runner_factory):
    """Part 44: a BYO run counts its tool calls against maxToolCalls, before any write."""
    env = await _seed(runner_factory)
    run_id = await _stage_run(runner_factory, env)
    job = await _leased(exec_client, runner_factory, env)
    async with runner_factory() as db:
        await db.execute(
            text("INSERT INTO run_execution_limits (run_id, max_tool_calls) VALUES (:r, 2)"),
            {"r": run_id},
        )
        await db.commit()

    # Fill exactly to the cap: two calls + their results land.
    ok = await _post(exec_client, job, "events", {"events": [
        _call("c1", CMD_A), _result("c1"), _call("c2", CMD_B), _result("c2")]})
    assert ok.status_code == 200, ok.text
    assert len(await _log(runner_factory, run_id, "tool_call")) == 2

    # A third call exceeds the cap; the whole batch is refused and NOTHING is written.
    over = await _post(exec_client, job, "events", {"events": [_call("c3", CMD_A)]})
    assert over.status_code == 409, over.text
    assert over.json()["detail"]["error"]["code"] == "TOOL_CALL_LIMIT"
    assert len(await _log(runner_factory, run_id, "tool_call")) == 2

    # Negative control: no limits row means no cap, not a hidden default.
    async with runner_factory() as db:
        await db.execute(
            text("DELETE FROM run_execution_limits WHERE run_id = :r"), {"r": run_id})
        await db.commit()
    free = await _post(exec_client, job, "events", {"events": [_call("c3", CMD_A)]})
    assert free.status_code == 200, free.text


async def test_events_refuse_foreign_unleased_and_stopped_runs(exec_client, runner_factory):
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env)
    job = await _leased(exec_client, runner_factory, env)
    thief = await _register(exec_client, env["foreignAdmin"]["token"], name="thief")
    stolen = await _post(exec_client, job, "events", {"events": [_call("c1", CMD_A)]},
                         token=thief["token"])
    assert stolen.status_code == 404
    assert await _log(runner_factory, job["runId"], "tool_call") == []

    # A human stop lands as a PAUSED(user_requested_stop) tombstone in the log.
    async with runner_factory() as db:
        project = (await db.execute(select(AgentRun))).scalar_one().project_id
        db.add(AgentEvent(project_id=project, run_id=job["runId"], type="paused",
                          payload={"runId": job["runId"], "reason": "user_requested_stop"}))
        await db.commit()
    stopped = await _post(exec_client, job, "events", {"events": [_call("c1", CMD_A)]})
    assert stopped.status_code == 409
    assert stopped.json()["detail"]["error"]["code"] == "RUN_NOT_EXECUTING"


# --------------------------------------------------------------- heartbeat


async def test_job_heartbeat_tells_the_runner_to_cancel_a_stopped_run(exec_client, runner_factory):
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env)
    job = await _leased(exec_client, runner_factory, env)
    live = await _post(exec_client, job, "heartbeat")
    assert live.status_code == 200 and live.json()["cancel"] is False

    async with runner_factory() as db:
        project = (await db.execute(select(AgentRun))).scalar_one().project_id
        db.add(AgentEvent(project_id=project, run_id=job["runId"], type="paused",
                          payload={"runId": job["runId"], "reason": "user_requested_stop"}))
        await db.commit()
    stopped = await _post(exec_client, job, "heartbeat")
    assert stopped.status_code == 200 and stopped.json()["cancel"] is True


# ------------------------------------------------------------------ finish


async def test_finish_completed_drives_the_run_to_engine_completed(exec_client, runner_factory):
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env)
    job = await _leased(exec_client, runner_factory, env)
    await _run_all_ok(exec_client, job)

    done = await _post(exec_client, job, "finish", {"status": "completed"})
    assert done.status_code == 200, done.text
    assert done.json()["state"] == "completed"

    (event,) = await _log(runner_factory, job["runId"], "engine_completed")
    assert event.payload["runnerId"] == job["runnerId"]
    assert "executed 2 approved command" in event.payload["summary"]
    async with runner_factory() as db:
        claims = (await db.execute(text("SELECT COUNT(*) FROM run_claims WHERE run_id = :r"),
                                   {"r": job["runId"]})).scalar_one()
        stored = (await db.execute(select(RunnerJob))).scalar_one()
    assert claims == 0 and stored.state == "completed"


async def test_finish_cannot_claim_success_the_log_does_not_show(exec_client, runner_factory):
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env)
    job = await _leased(exec_client, runner_factory, env)
    await _post(exec_client, job, "events", {"events": [_call("c1", CMD_A), _result("c1")]})

    done = await _post(exec_client, job, "finish", {"status": "completed"})
    assert done.status_code == 200
    assert done.json()["state"] == "failed"  # CMD_B never reported
    assert await _log(runner_factory, job["runId"], "engine_completed") == []
    (event,) = await _log(runner_factory, job["runId"], "engine_failed")
    assert CMD_B in event.payload["reason"]


async def test_a_failing_command_settles_the_run_failed(exec_client, runner_factory):
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env)
    job = await _leased(exec_client, runner_factory, env)
    await _post(exec_client, job, "events", {"events": [
        _call("c1", CMD_A), _result("c1", ok=False, exit_code=1, output="1 failed"),
        _call("c2", CMD_B), _result("c2"),
    ]})
    done = await _post(exec_client, job, "finish", {"status": "completed"})
    assert done.json()["state"] == "failed"
    (event,) = await _log(runner_factory, job["runId"], "engine_failed")
    assert CMD_A in event.payload["reason"]


async def test_finish_failed_drives_engine_failed(exec_client, runner_factory):
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env)
    job = await _leased(exec_client, runner_factory, env)
    done = await _post(exec_client, job, "finish", {"status": "failed"})
    assert done.status_code == 200 and done.json()["state"] == "failed"
    assert len(await _log(runner_factory, job["runId"], "engine_failed")) == 1
    again = await _post(exec_client, job, "finish", {"status": "failed"})
    assert again.status_code == 409  # settled once: no second ENGINE_* event
    assert len(await _log(runner_factory, job["runId"], "engine_failed")) == 1


async def test_finish_never_resurrects_a_stopped_run(exec_client, runner_factory):
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env)
    job = await _leased(exec_client, runner_factory, env)
    await _run_all_ok(exec_client, job)
    async with runner_factory() as db:
        project = (await db.execute(select(AgentRun))).scalar_one().project_id
        db.add(AgentEvent(project_id=project, run_id=job["runId"], type="paused",
                          payload={"runId": job["runId"], "reason": "user_requested_stop"}))
        await db.commit()

    done = await _post(exec_client, job, "finish", {"status": "completed"})
    assert done.status_code == 200
    assert done.json()["state"] == "failed" and done.json()["runState"] == "not_executing"
    assert await _log(runner_factory, job["runId"], "engine_completed", "engine_failed") == []
    async with runner_factory() as db:
        claims = (await db.execute(text("SELECT COUNT(*) FROM run_claims WHERE run_id = :r"),
                                   {"r": job["runId"]})).scalar_one()
    assert claims == 0  # the lease is still released


# ------------------------------------------------------------ pre-start offer


async def test_a_run_can_be_offered_before_it_starts(exec_client, runner_factory):
    """No run_claims row yet (start_execution has not run): the offer is accepted for
    the admin's own team only, so the orchestrator can dispatch it to the host."""
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env, lifecycle=())
    async with runner_factory() as db:
        await db.execute(text("DELETE FROM run_claims WHERE run_id = :r"), {"r": env["runA"]})
        await db.commit()
    created = await _register(exec_client, env["admin"]["token"])
    ok = await _assign(exec_client, env["admin"]["token"], created["runner"]["id"], env["runA"])
    assert ok.status_code == 201, ok.text
    assert ok.json()["job"]["state"] == "offered"

    thief = await _register(exec_client, env["foreignAdmin"]["token"], name="thief")
    foreign = await _assign(exec_client, env["foreignAdmin"]["token"], thief["runner"]["id"],
                            env["runA"])
    unknown = await _assign(exec_client, env["foreignAdmin"]["token"], thief["runner"]["id"],
                            "z" * 36)
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json()
