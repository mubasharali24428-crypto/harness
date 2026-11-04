"""W22: after the worker loop is frozen past the heartbeat timeout, it must let
its own runs heartbeat before it reaps (2026-10-30 Mac-sleep incident)."""

import asyncio

import pytest

from harness_orchestrator import worker as worker_mod
from harness_orchestrator.worker import RunQueueWorker


class _ClockedService:
    """Each poll advances a fake wall clock 1 s; poll 3 also 'sleeps' 600 s."""

    def __init__(self, clock):
        self.clock = clock
        self.log = []

    async def recover_orphans(self):
        return []

    async def reap_zombies(self):
        self.log.append("reap")
        return []

    async def poll_once(self, max_claims=None):
        self.log.append("poll")
        polls = self.log.count("poll")
        self.clock[0] += 601.0 if polls == 3 else 1.0
        if polls == 40:
            raise asyncio.CancelledError
        return []


def test_reaping_is_deferred_after_a_suspend(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(worker_mod.time, "time", lambda: clock[0])
    svc = _ClockedService(clock)
    w = RunQueueWorker(svc, poll_interval=0.001, claim_batch=8, min_poll_seconds=0.001)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(w._loop())
    cycles = "".join("R" if e == "reap" else "." for e in svc.log).split(".")[:-1]
    reaped = [c == "R" for c in cycles]
    assert reaped[:3] == [True, True, True], "normal cycles reap every time"
    assert reaped[3] is False, "first cycle after the suspend must not reap"
    resumed = reaped.index(True, 3)
    assert 20 <= resumed - 3 <= 30, f"reaping resumes after the grace, not never: {reaped}"
