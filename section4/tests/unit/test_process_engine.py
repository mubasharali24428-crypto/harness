"""A2-S3 F023: process isolation, kill-on-timeout, confinement, durable ask."""

import asyncio
import json
import os
import sys
import time

import pytest

from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.engine_runtime import EngineRuntime, EngineRunTimeout
from harness_orchestrator.process_engine import (
    ConfinementError,
    ProcessEngine,
    per_run_workspace,
)

ECHO_CHILD = """
import json, os, sys
req = json.loads(sys.stdin.read())
out = {"sessionId": req["sessionId"], "engine": "child", "status": "completed",
       "finalResponse": json.dumps({"key": os.environ.get("DEEPSEEK_API_KEY"),
       "cwd": os.getcwd(), "leak": os.environ.get("SECRET_PARENT_VAR")})}
print("@@RESULT@@" + json.dumps(out))
"""

HANG_CHILD = """
import subprocess, sys, time, os
sub = subprocess.Popen([sys.executable, "-c",
  "import time,os,sys\\nwhile True:\\n open(sys.argv[1],'a').write('x'); time.sleep(0.05)",
  os.environ["DSH_WORKSPACE"] + "/tick"])
sys.stdin.read()
time.sleep(60)
"""


def _engine(tmp_path, script, key):
    s = tmp_path / "child.py"
    s.write_text(script)
    w = DeepSeekHarnessWrapper(
        workspace_dir=str(tmp_path / "ws"),
        session_root=str(tmp_path / "ss"),
        api_key=key,
        live_mode="dry",
    )
    return ProcessEngine(w, argv=[sys.executable, str(s)], cleanup=False, enforce_confinement=False)


def test_concurrent_runs_get_distinct_keys_and_workspaces(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_PARENT_VAR", "parent-secret")
    a, b = _engine(tmp_path, ECHO_CHILD, "key-A"), _engine(tmp_path, ECHO_CHILD, "key-B")

    async def go():
        return await asyncio.gather(a.run_isolated("g", "run-a"), b.run_isolated("g", "run-b"))

    ra, rb = asyncio.run(go())
    da, db = json.loads(ra["finalResponse"]), json.loads(rb["finalResponse"])
    assert (da["key"], db["key"]) == ("key-A", "key-B")
    assert da["cwd"] != db["cwd"] and "run-a" in da["cwd"]
    assert da["leak"] is None and db["leak"] is None  # no inherited parent secrets
    assert "DEEPSEEK_API_KEY" not in os.environ  # parent env never mutated


def test_timeout_kills_child_and_grandchild_no_post_stop_writes(tmp_path):
    eng = _engine(tmp_path, HANG_CHILD, "k")
    rt = EngineRuntime(max_concurrency=1, run_timeout_seconds=1.5)
    with pytest.raises(EngineRunTimeout):
        asyncio.run(rt.run(eng, goal="g", session_id="run-h"))
    tick = tmp_path / "ws" / "run-h" / "tick"
    assert tick.exists()  # grandchild really ran
    size = tick.stat().st_size
    time.sleep(0.5)
    assert tick.stat().st_size == size  # nothing writes after stop
    with pytest.raises(ProcessLookupError):
        os.killpg(eng.last_pgid, 0)


def test_child_crash_is_error_result_not_exception(tmp_path):
    eng = _engine(tmp_path, "import sys; sys.exit(3)", "secret-key-123")
    r = asyncio.run(eng.run_isolated("g", "run-c"))
    assert r["status"] == "error" and "secret-key-123" not in r["finalResponse"]


@pytest.mark.parametrize("bad", ["../x", "a/b", "", ".", "..", "x" * 65, "a\0b"])
def test_confinement_rejects_unsafe_run_ids(tmp_path, bad):
    with pytest.raises(ConfinementError):
        per_run_workspace(tmp_path, bad)


def test_confinement_rejects_relative_and_root(tmp_path):
    for root in ("relative/dir", "/"):
        with pytest.raises(ConfinementError):
            per_run_workspace(root, "run-1")


def test_symlinked_run_dir_escape_is_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside").mkdir()
    (root / "run-x").symlink_to(tmp_path / "outside")
    with pytest.raises(ConfinementError):
        per_run_workspace(root, "run-x")


def test_cancel_task_kills_process_group(tmp_path):
    """F027: stop/pause cancels the awaiting task; the child tree dies with it."""
    eng = _engine(tmp_path, HANG_CHILD, "k")

    async def go():
        t = asyncio.create_task(eng.run_isolated("g", "run-s"))
        await asyncio.sleep(1.0)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    asyncio.run(go())
    tick = tmp_path / "ws" / "run-s" / "tick"
    size = tick.stat().st_size
    time.sleep(0.4)
    assert tick.stat().st_size == size
    with pytest.raises(ProcessLookupError):
        os.killpg(eng.last_pgid, 0)


def test_live_run_fails_closed_without_confinement_proof(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGINE_ALLOW_UNCONFINED_LIVE", raising=False)
    w = DeepSeekHarnessWrapper(workspace_dir=str(tmp_path), api_key="k", live_mode="dry")
    r = asyncio.run(ProcessEngine(w).run_isolated("g", "run-z"))
    assert r["errorCode"] == "ENGINE_CONFINEMENT_UNPROVEN" and r["status"] == "error"
    assert not (tmp_path / "run-z").exists()  # nothing spawned, nothing created
