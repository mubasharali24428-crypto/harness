# ruff: noqa: F811 - imported fixtures are re-requested as test parameters
"""Part 09 (005, 006, 010): workspace identity in run metadata, safe cleanup after
terminal states. Real isolated engine with the stand-in worker (no model, no network)."""

import asyncio
import json
import os
import re
from pathlib import Path

import pytest

from harness_orchestrator import confine, domain_metrics, retention
from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.service import OrchestratorService
from tests.unit.test_retention import NOW, q  # noqa: F401
from tests.unit.test_retention import env as retention_env  # noqa: F401 - fixture alias

HERE = Path(__file__).resolve().parent
SECTION4 = HERE.parents[1]
KEY = "sk-realistic-0123456789abcdef"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ENGINE_WORKER_MODULE", "standin_worker")
    monkeypatch.setenv("PYTHONPATH", f"{HERE}:{SECTION4}/src:{SECTION4.parent}/contracts/src")
    monkeypatch.setenv("HARNESS_ENGINE_CONFINEMENT", "off")
    monkeypatch.setenv("HARNESS_MASTER_KEY", "mk-secret-value")
    root = Path(os.path.realpath(tmp_path))
    engine = DeepSeekHarnessWrapper(
        workspace_dir=str(root / "ws"),
        session_root=str(root / "sess"),
        api_key=KEY,
        base_url="http://127.0.0.1:9/v1",
        model="deepseek-chat",
        live_mode="live",
        isolated=True,
    )
    return OrchestratorService(engine=engine), root


async def _run(svc, run_id, goal):
    plan_id = await svc.propose_plan(run_id, goal=goal)
    await svc.approve_plan(run_id, plan_id, "u1")
    await svc.start_execution(run_id)
    return svc.runs[run_id]["task"]


async def test_run_metadata_names_the_workspace_without_paths_or_secrets(isolated):
    svc, root = isolated
    task = await _run(svc, "ident-run", "write:out.txt")
    await asyncio.wait_for(task, timeout=30)
    agg = await svc.get_status("ident-run")
    ws = agg["workspace"]
    assert ws["id"] == "ident-run" and ws["isolated"] is True
    assert re.fullmatch(r"[0-9a-f]{16}", ws["fingerprint"])
    assert ws["isolation"] == confine.resolve_mode()
    blob = json.dumps(agg, default=str)
    for secret in (KEY, "mk-secret-value", str(root), "DEEPSEEK_API_KEY", "HARNESS_MASTER_KEY"):
        assert secret not in blob
    other = svc.engine.workspace_identity("other-run")
    assert other["id"] != ws["id"] and other["fingerprint"] != ws["fingerprint"]


async def test_non_isolated_engine_reports_no_workspace():
    svc = OrchestratorService(engine=DeepSeekHarnessWrapper(backend_runner=lambda g: "ok"))
    plan_id = await svc.propose_plan("plain-run", goal="x")
    await svc.approve_plan("plain-run", plan_id, "u1")
    assert (await svc.get_status("plain-run"))["workspace"] is None


async def test_stop_removes_scratch_but_keeps_outputs(isolated):
    svc, root = isolated
    task = await _run(svc, "clean-run", "slow")
    scratch = root / "ws" / ".run-tmp" / "clean-run"
    for _ in range(300):  # wait for the worker to be up (it creates its scratch dirs)
        if scratch.is_dir():
            break
        await asyncio.sleep(0.02)
    assert scratch.is_dir()
    (root / "ws" / "clean-run" / "result.txt").write_text("approved output")
    assert await svc.stop_agent("clean-run") is True
    await asyncio.wait_for(asyncio.shield(_settle(task)), timeout=30)
    assert not scratch.exists()  # scratch is gone once the worker is confirmed dead
    assert (root / "ws" / "clean-run" / "result.txt").read_text() == "approved output"
    agg = await svc.get_status("clean-run")
    assert agg["lifecycle"]["phase"] == "stopped" and agg["lifecycle"]["mutationPossible"] is False


async def test_cleanup_refuses_while_the_worker_is_alive(isolated):
    svc, root = isolated
    task = await _run(svc, "live-run", "slow")
    scratch = root / "ws" / ".run-tmp" / "live-run"
    for _ in range(300):
        if scratch.is_dir():
            break
        await asyncio.sleep(0.02)
    assert svc.engine.is_worker_running("live-run")
    assert svc.engine.cleanup_scratch("live-run") is False and scratch.is_dir()
    await svc.stop_agent("live-run")
    await asyncio.wait_for(asyncio.shield(_settle(task)), timeout=30)


def test_cleanup_refuses_symlinked_scratch_and_counts_it(isolated, tmp_path):
    svc, root = isolated
    domain_metrics.reset()
    victim = root / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("x")
    (root / "ws" / ".run-tmp").mkdir(parents=True)
    (root / "ws" / ".run-tmp" / "evil-run").symlink_to(victim)
    assert svc.engine.cleanup_scratch("evil-run") is False
    assert (victim / "keep.txt").exists()
    assert domain_metrics.value("harness_isolation_violation_total", "symlink_escape") == 1


def test_retention_also_clears_scratch_and_refuses_symlinked_dirs(retention_env, tmp_path):
    f, ws, sess = retention_env
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("x")
    for rid in ("old-done", "old-stopped"):
        d = Path(ws) / ".run-tmp" / rid
        d.mkdir(parents=True)
        (d / "t").write_text("scratch")
    # a hostile symlink where a purge target should be: must not be followed
    (Path(ws) / ".staged" / "old-stopped").rename(Path(ws) / "moved")
    (Path(ws) / ".staged" / "old-stopped").symlink_to(victim)
    asyncio.run(retention.purge(f, ws, sess, days=30, apply=True, now=NOW))
    assert not (Path(ws) / ".run-tmp" / "old-stopped").exists()
    assert (Path(ws) / ".run-tmp" / "old-done").is_dir()  # held run: scratch preserved
    assert (victim / "keep.txt").exists()  # symlink target untouched
    assert (Path(ws) / "old-done").is_dir()  # outputs preserved by default
    assert q(f, "SELECT COUNT(*) AS n FROM audit_log")[0].n == 2  # audit never purged


async def _settle(task):
    try:
        await task
    except asyncio.CancelledError:
        pass
