# ruff: noqa: F811, F401
"""B2-B4 end to end at the service/API level: killable stop, staged approvals, apply."""

import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness_orchestrator import api as orch_api
from harness_orchestrator.state_machine import AgentState
from tests.unit.test_collab_backend import P, audit_actions, env, lead_run, q
from tests.unit.test_isolation_and_staging import alive, engine, worker_env


def setup_service(tmp_path, seeds):
    svc = orch_api._service
    svc._budget_gate = AsyncMock(return_value=False)
    eng = engine(tmp_path)
    svc.engine = eng
    ws = Path(eng.run_workspace("r1"))
    ws.mkdir(parents=True, exist_ok=True)
    for name, body in seeds.items():
        (ws / name).write_text(body)
    return svc, eng, ws


def run_goal(svc, goal):
    async def go():
        rd = await svc._ensure_run("r1")
        rd["state_machine"].state = AgentState.EXECUTING
        await svc._execute(rd, SimpleNamespace(goal=goal))
        return rd

    return asyncio.run(go())


def test_high_impact_delete_is_withheld_then_applied_after_leader_approval(
    env, worker_env, tmp_path
):
    as_, f = env
    lead_run(as_)
    svc, eng, ws = setup_service(tmp_path, {"gone.txt": "delete me\n", "keep.txt": "k\n"})
    rd = run_goal(svc, "delete:gone.txt")
    assert (ws / "gone.txt").exists(), "deletion must not reach the workspace unapproved"
    assert rd["staged"]["withheld"][0]["paths"] == ["gone.txt"]
    appr = [
        a
        for a in as_("lead").get(f"{P}/r1/approvals").json()["approvals"]
        if a["state"] == "pending"
    ]
    assert (
        len(appr) == 1
        and appr[0]["requester"] == "system:staging"
        and appr[0]["action"] == "delete"
    )
    a = appr[0]
    # cannot land while pending, nor with a stale digest, nor by a non-leader
    assert (
        as_("lead")
        .post(f"{P}/r1/approvals/{a['id']}/apply", json={"digest": a["digest"]})
        .status_code
        == 409
    )
    assert (
        as_("eng2")
        .post(f"{P}/r1/approvals/{a['id']}/decision", json={"approve": True, "digest": a["digest"]})
        .status_code
        == 403
    )
    assert (
        as_("lead")
        .post(f"{P}/r1/approvals/{a['id']}/decision", json={"approve": True, "digest": a["digest"]})
        .status_code
        == 200
    )
    assert (
        as_("eng2")
        .post(f"{P}/r1/approvals/{a['id']}/apply", json={"digest": a["digest"]})
        .status_code
        == 403
    )
    assert (
        as_("lead").post(f"{P}/r1/approvals/{a['id']}/apply", json={"digest": "0" * 64}).status_code
        == 409
    )
    assert (ws / "gone.txt").exists()
    r = as_("lead").post(f"{P}/r1/approvals/{a['id']}/apply", json={"digest": a["digest"]})
    assert r.status_code == 200 and r.json()["state"] == "completed", r.text
    assert not (ws / "gone.txt").exists() and (ws / "keep.txt").exists()
    assert "approval.completed" in audit_actions(f)


def test_large_rewrite_withheld_and_new_run_after_landing_does_not_rewithhold(
    env, worker_env, tmp_path
):
    as_, f = env
    lead_run(as_)
    svc, eng, ws = setup_service(tmp_path, {"big.py": "".join(f"line {i}\n" for i in range(80))})
    run_goal(svc, "rewrite:big.py")
    assert (ws / "big.py").read_text().startswith("line 0")
    a = [
        x
        for x in as_("lead").get(f"{P}/r1/approvals").json()["approvals"]
        if x["state"] == "pending"
    ][0]
    assert "REWRITTEN 0" in a["diff"]
    as_("lead").post(
        f"{P}/r1/approvals/{a['id']}/decision", json={"approve": True, "digest": a["digest"]}
    )
    assert (
        as_("lead")
        .post(f"{P}/r1/approvals/{a['id']}/apply", json={"digest": a["digest"]})
        .status_code
        == 200
    )
    assert (ws / "big.py").read_text().startswith("REWRITTEN 0")
    rd = run_goal(svc, "noop")
    assert rd["staged"]["withheld"] == []


def test_refiled_staged_approvals_renew_and_void_stale_ones(env, worker_env, tmp_path):
    as_, f = env
    lead_run(as_)
    svc, eng, ws = setup_service(tmp_path, {"a.txt": "a\n", "b.txt": "b\n"})
    run_goal(svc, "delete:a.txt")
    first = as_("lead").get(f"{P}/r1/approvals").json()["approvals"][0]
    (ws / "b.txt").unlink()  # a second deletion lands in the workspace ...

    async def restage():
        return await svc._stage_outputs(await svc._ensure_run("r1"))

    assert asyncio.run(restage()) is None  # ... and is staged -> a different group digest
    assert as_("lead").post(f"{P}/r1/staged/approvals").status_code == 200  # idempotent re-file
    states = {x["id"]: x["state"] for x in as_("lead").get(f"{P}/r1/approvals").json()["approvals"]}
    assert states[first["id"]] == "invalidated"
    assert sum(1 for s in states.values() if s == "pending") == 1


def test_stop_kills_worker_confirms_and_reports_stopped(env, worker_env, tmp_path):
    as_, f = env
    lead_run(as_)
    svc, eng, ws = setup_service(tmp_path, {})
    pidfile = ws / "child.pid"

    async def go():
        rd = await svc._ensure_run("r1")
        rd["state_machine"].state = AgentState.EXECUTING
        task = asyncio.create_task(svc._execute(rd, SimpleNamespace(goal="slow")))
        rd["task"] = task
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text():
                break
            await asyncio.sleep(0.05)
        child = int(pidfile.read_text())
        assert eng.is_worker_running("r1") and not svc.engine_quiesced("r1")
        t0 = time.monotonic()
        await svc.stop_agent("r1")
        return child, time.monotonic() - t0, svc.engine_quiesced("r1")

    child, took, quiet = asyncio.run(go())
    assert quiet is True and took < 6
    time.sleep(0.3)
    assert not alive(child)
