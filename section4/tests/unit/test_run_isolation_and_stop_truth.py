"""Multiplayer pilot S2: per-run workspace isolation + truthful cancellation."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import types

import pytest

from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.engine_runtime import EngineRuntime, EngineRunTimeout


def _engine(tmp_path):
    return DeepSeekHarnessWrapper(
        workspace_dir=str(tmp_path / "ws"),
        session_root=str(tmp_path / "sess"),
        api_key="k",
        live_mode="dry",
    )


def test_distinct_runs_get_distinct_workspaces(tmp_path):
    e = _engine(tmp_path)
    a, b = e.run_workspace("run-a"), e.run_workspace("run-b")
    assert a != b
    root = str(tmp_path.resolve() / "ws")
    assert a.startswith(root) and b.startswith(root)


@pytest.mark.parametrize("bad", ["", "..", "../x", "a/b", "/abs", ".hidden", "a" * 65])
def test_bad_session_ids_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        _engine(tmp_path).run_workspace(bad)


def test_symlink_escape_rejected(tmp_path):
    e = _engine(tmp_path)
    (tmp_path / "ws").mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "ws" / "evil").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError):
        e.run_workspace("evil")


def test_live_run_uses_per_run_cwd_and_bad_id_fails_closed(tmp_path, monkeypatch):
    seen: list[str] = []

    class Fake:
        def __init__(self, **kw):
            seen.append(kw["cwd"])

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, goal, session_id):
            return types.SimpleNamespace(final_response="ok", events=[])

    fake_mod = types.SimpleNamespace(DeepSeekHarness=Fake)
    monkeypatch.setitem(sys.modules, "deepseek_harness", fake_mod)
    e = _engine(tmp_path)
    assert e._run_live_events("g", "run-1")[1] == "completed"
    assert e._run_live_events("g", "run-2")[1] == "completed"
    assert seen[0] != seen[1]

    seen.clear()
    assert e._run_live_events("g", "../escape")[1] == "error"
    assert seen == []  # SDK never constructed


def test_is_running_tracks_thread_not_await():
    """A timed-out/cancelled await must not look quiesced while the thread lives."""
    release = threading.Event()

    class Eng:
        model = "m"

        def execute_goal(self, goal, session_id):
            release.wait(5)
            return {"status": "completed"}

    async def go():
        rt = EngineRuntime(max_concurrency=1, run_timeout_seconds=0.1)
        with pytest.raises(EngineRunTimeout):
            await rt.run(Eng(), goal="g", session_id="r1")
        still = rt.is_running("r1")  # await is over, thread is not
        release.set()
        for _ in range(50):
            if not rt.is_running("r1"):
                break
            await asyncio.sleep(0.02)
        return still, rt.is_running("r1")

    still, after = asyncio.run(go())
    assert still is True and after is False


def test_second_stop_on_finished_run_is_a_noop():
    """Found live (S4): a repeated stop raised InvalidStateTransitionError -> HTTP 500."""
    from harness_orchestrator.service import OrchestratorService
    from harness_orchestrator.state_machine import AgentState

    async def go():
        svc = OrchestratorService()
        await svc._ensure_run("r-stop")
        await svc.stop_agent("r-stop")
        sm = svc.runs["r-stop"]["state_machine"] if "r-stop" in svc.runs else None
        await svc.stop_agent("r-stop")  # must not raise
        return sm.state if sm else AgentState.DONE

    assert asyncio.run(go()) == AgentState.DONE


def _fake_sdk(monkeypatch, seen):
    class Fake:
        def __init__(self, **kw):
            seen.append(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, goal, session_id):
            return types.SimpleNamespace(final_response="ok", events=[])

    monkeypatch.setitem(
        sys.modules, "deepseek_harness", types.SimpleNamespace(DeepSeekHarness=Fake)
    )


def test_sdk_call_uses_real_surface_and_restricted_profile(tmp_path, monkeypatch):
    """SDK 0.2.1rc2 has no cordis/session_root; it needs dsh_home and a profile."""
    seen: list[dict] = []
    _fake_sdk(monkeypatch, seen)
    monkeypatch.delenv("DSH_PROFILE", raising=False)
    monkeypatch.delenv("DSH_PATCH_FILES", raising=False)
    e = DeepSeekHarnessWrapper(
        workspace_dir=str(tmp_path / "ws"),
        session_root=str(tmp_path / "sess"),
        api_key="sk-realistic-0123456789abcdef",
        base_url="http://127.0.0.1:9/v1",
        live_mode="dry",
        isolated=False,
    )
    assert e._run_live_events("g", "run-sdk")[1] == "completed"
    kw = seen[0]
    assert "cordis" not in kw and "session_root" not in kw
    assert kw["profile"] == "sdk-minimal"  # never the default danger-full-access composition
    assert kw["dsh_home"].endswith("/run-sdk") and kw["cwd"].endswith("/run-sdk")
    assert kw["api_key"] == "sk-realistic-0123456789abcdef" and kw["base_url"].endswith("/v1")
    assert "DEEPSEEK_API_KEY" not in os.environ or os.environ["DEEPSEEK_API_KEY"] != kw["api_key"]


def test_missing_patch_file_fails_closed(tmp_path, monkeypatch):
    seen: list[dict] = []
    _fake_sdk(monkeypatch, seen)
    monkeypatch.setenv("DSH_PATCH_FILES", str(tmp_path / "nope.patch.yml"))
    e = DeepSeekHarnessWrapper(
        workspace_dir=str(tmp_path / "ws"),
        session_root=str(tmp_path / "sess"),
        live_mode="dry",
        isolated=False,
    )
    text_, status, *_ = e._run_live_events("g", "run-p")
    assert status == "error" and "not found" in text_ and seen == []
