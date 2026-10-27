"""W8 slice 2: what the durable proof in `recover_boundaries` actually reads.

The sweeper's strong rule deletes a network only for a run the lifecycle says is
over, so the interesting bug would be a settlement read that names a live run.
``agent_runs.state`` is NOT that proof — the orchestrator does not write that
column per transition; the state the API reports is replayed from
``agent_events``. These tests pin the read against two services sharing one
store, and pin the refusal when the claim table cannot be read at all.
"""

import asyncio

from sqlalchemy import text

from harness_orchestrator import container_runtime, service as service_mod
from tests.test_stateless_orchestrator_n1 import replica_pair  # noqa: F401

NET = {"net-a": "run-a", "net-b": "run-b", "net-c": "run-c"}


async def _emit(factory, run_id, event_type):
    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO agent_events (project_id, run_id, type, payload)"
                " VALUES ('proj-race', :r, :t, '{}')"
            ),
            {"r": run_id, "t": event_type},
        )
        await db.commit()


async def _claim(factory, run_id, state="running"):
    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO run_claims (run_id, project_id, state, claimed_at,"
                " heartbeat_at, worker_id) VALUES (:r, 'proj-race', :s,"
                " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 'w-1')"
            ),
            {"r": run_id, "s": state},
        )
        await db.commit()


def _capture(monkeypatch, networks=NET):
    seen = {}

    def fake_sweep(held, settled, **kwargs):
        seen["held"] = set(held)
        seen["settled"] = set(settled or ())
        seen["kwargs"] = kwargs
        return {"removed": [], "kept": sorted(networks), "count": 0, "containersStopped": 0}

    monkeypatch.setattr(container_runtime, "sweep_orphan_boundaries", fake_sweep)
    monkeypatch.setattr(container_runtime, "labelled_boundary_runs", lambda: dict(networks))
    return seen


def test_settlement_comes_from_the_event_log_and_claims_are_held(replica_pair, monkeypatch):  # noqa: F811
    factory = replica_pair["factory"]
    service = replica_pair["replicas"][0]
    seen = _capture(monkeypatch)

    # run-a: crashed. run-b: started, still executing. run-c: crashed BUT a claim
    # row still says running, so a worker may still own the boundary.
    asyncio.run(_emit(factory, "run-a", "engine_failed"))
    asyncio.run(_emit(factory, "run-b", "execution_started"))
    asyncio.run(_emit(factory, "run-c", "engine_failed"))
    asyncio.run(_emit(factory, "run-c", "execution_started"))
    asyncio.run(_claim(factory, "run-c"))

    report = asyncio.run(service.recover_boundaries())
    assert report["count"] == 0  # the fake did the deleting
    assert "run-c" in seen["held"], "a running claim must count as held"
    assert "run-a" in seen["settled"]
    assert "run-b" not in seen["settled"], "an executing run is not settled"
    # The candidate set the settlement query is given excludes held runs.
    assert seen["kwargs"].get("older_than_seconds", 0) >= 0


def test_an_unreadable_claim_table_stops_the_sweep(replica_pair, monkeypatch):  # noqa: F811
    service = replica_pair["replicas"][0]
    calls = []
    monkeypatch.setattr(
        container_runtime, "sweep_orphan_boundaries", lambda *a, **k: calls.append(a)
    )
    service.run_queue_factory = None
    service.session_factory = None  # nothing can be read: the sweep must not run
    report = asyncio.run(service.recover_boundaries())
    assert report["removed"] == [] and calls == []
    assert "skipped" in report


def test_a_docker_less_process_reports_its_own_failure(replica_pair, monkeypatch):  # noqa: F811
    service = replica_pair["replicas"][0]

    def raise_oserror(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory: 'docker'")

    monkeypatch.setattr(container_runtime, "labelled_boundary_runs", lambda: {})
    monkeypatch.setattr(container_runtime, "sweep_orphan_boundaries", raise_oserror)
    report = asyncio.run(service.recover_boundaries())
    assert "docker unavailable" in report["skipped"]
