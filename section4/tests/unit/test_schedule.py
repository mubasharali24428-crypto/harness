"""Scheduled maintenance jobs: the loop, the job table, and the deploy wiring."""

import ast
import asyncio
import logging
from pathlib import Path

import pytest

from harness_orchestrator import (
    artifacts,
    collab,
    discussion,
    exports,
    schedule,
    search,
    session_purge,
    workspace_gate,
)

SERVICES = Path(__file__).resolve().parents[3] / "deploy" / "runners" / "services.py"


async def _until(cond, tries=300):
    for _ in range(tries):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


async def test_a_job_repeats_survives_a_failing_tick_and_stops_on_cancel(caplog):
    calls = []

    async def job():
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger="harness_orchestrator.schedule"):
        task = asyncio.create_task(schedule.run_periodically("demo", job, 0.01))
        assert await _until(lambda: len(calls) >= 4)  # kept going past the failure at call 2
        task.cancel()
        done = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(done[0], asyncio.CancelledError)
    assert [r.getMessage() for r in caplog.records if "demo" in r.getMessage()] == [
        "scheduled job demo failed"
    ]


def test_every_maintenance_function_is_scheduled_by_default():
    jobs = schedule.maintenance_jobs(object(), env={})
    assert {label: every for label, _, every in jobs} == {
        "search-sync": 60.0,
        "export-purge": 3600.0,
        "discussion-purge": 3600.0,
        "artifact-purge": 3600.0,
        "workspace-purge": 3600.0,
        "session-purge": 3600.0,  # part 40 (session 12)
        "lease-sweep": 30.0,  # part 42 (session 12)
    }


def test_zero_or_negative_turns_one_job_off_and_the_rest_keep_running():
    env = {"HARNESS_EXPORT_PURGE_SECONDS": "0", "HARNESS_SEARCH_SYNC_SECONDS": "-1",
           "HARNESS_ARTIFACT_PURGE_SECONDS": "7"}
    jobs = {label: every for label, _, every in schedule.maintenance_jobs(object(), env=env)}
    assert jobs == {"discussion-purge": 3600.0, "artifact-purge": 7.0, "workspace-purge": 3600.0,
                    "session-purge": 3600.0, "lease-sweep": 30.0}


def test_a_garbage_interval_fails_at_startup_not_silently():
    with pytest.raises(ValueError):
        schedule.maintenance_jobs(object(), env={"HARNESS_SEARCH_SYNC_SECONDS": "soon"})


async def test_each_job_calls_its_function_with_the_session_factory(monkeypatch):
    seen = []

    def recorder(tag):
        async def fake(f, *a, **k):
            seen.append((tag, f))

        return fake

    for mod, name in ((search, "sync_index"), (exports, "purge_expired_exports"),
                      (discussion, "purge_expired"), (artifacts, "purge_expired"),
                      (workspace_gate, "purge_expired_workspaces"),
                      (session_purge, "purge_due"), (collab, "sweep_expired_leases")):
        monkeypatch.setattr(mod, name, recorder(f"{mod.__name__.rsplit('.', 1)[-1]}.{name}"))
    factory = object()
    for _, call, _ in schedule.maintenance_jobs(factory, env={}):
        await call()
    assert seen == [("search.sync_index", factory), ("exports.purge_expired_exports", factory),
                    ("discussion.purge_expired", factory), ("artifacts.purge_expired", factory),
                    ("workspace_gate.purge_expired_workspaces", factory),
                    ("session_purge.purge_due", factory),
                    ("collab.sweep_expired_leases", factory)]


def _orchestrator_builder() -> ast.FunctionDef:
    tree = ast.parse(SERVICES.read_text())
    return next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "app_orchestrator"
    )


def _calls(node: ast.AST) -> list[tuple[int, str]]:
    return [(c.lineno, c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", ""))
            for c in ast.walk(node) if isinstance(c, ast.Call)]


def test_the_deployed_orchestrator_installs_the_route_limiter_and_starts_the_jobs():
    builder = _orchestrator_builder()
    calls = _calls(builder)
    limiter = [ln for ln, name in calls if name == "install_orchestrator_rate_limit"]
    context = [ln for ln, name in calls if name == "add_middleware"]
    assert len(limiter) == 1, "the orchestrator must install the route limiter exactly once"
    assert limiter[0] < min(context), "limiter goes in before RequestContext so a 429 has an id"
    names = {name for _, name in calls}
    assert {"maintenance_jobs", "run_periodically", "run_limit_sweeper"} <= names
    src = ast.unparse(builder)
    assert "MUTATION_ROUTE_LIMITS" in src and "ORCHESTRATOR_ROUTE_LIMITS" in src
    assert "task.cancel()" in src, "shutdown must cancel the background tasks"
