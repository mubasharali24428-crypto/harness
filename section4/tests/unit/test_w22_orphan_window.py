"""W22: the window in which a dead run's boundary outlives anyone who knows it.

The fast `/reap` loop lives in the orchestrator process, so it dies with that
process. What survives is the durable reaper: a claim goes stale, the run is
blocked with `engine_failed`, and from that moment the boundary is a corpse.
The sweep must read that proof in the same cycle — waiting for the ~10 minute
interval is what let an unfenced container run for ~15 minutes after a double
failure (`sweep grace + interval`).
"""

import asyncio

import pytest

from harness_orchestrator.worker import RunQueueWorker


class _ReapingService:
    """Reaps one run on its second call (so the startup sweep is already spent),
    then nothing; counts boundary sweeps."""

    def __init__(self):
        self.sweeps = 0
        self.polls = 0
        self.reaps = 0

    async def recover_orphans(self):
        return []

    async def reap_zombies(self):
        self.reaps += 1
        return ["run-dead"] if self.reaps == 2 else []

    async def recover_boundaries(self):
        self.sweeps += 1
        return {"count": 0}

    async def poll_once(self, max_claims=None):
        self.polls += 1
        if self.polls >= 6:
            raise asyncio.CancelledError
        return []


def _run(svc):
    worker = RunQueueWorker(svc, poll_interval=0.001, claim_batch=1, min_poll_seconds=0.001)
    worker.boundary_sweep_seconds = 600  # the interval must not be what triggers it
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker._loop())


def test_a_durably_reaped_run_sweeps_in_the_same_cycle():
    svc = _ReapingService()
    _run(svc)
    # One sweep at startup (last_sweep = 0) and exactly one more, right after the
    # reap. A per-cycle sweep would show ~5 here; no sweep after the reap would
    # leave the corpse to the 600 s interval.
    assert svc.sweeps == 2, f"startup + post-reap only, got {svc.sweeps} over {svc.polls} polls"
    assert svc.reaps >= 2


class _NothingToReap(_ReapingService):
    async def reap_zombies(self):
        return []


def test_an_empty_reap_does_not_short_circuit_the_interval():
    svc = _NothingToReap()
    _run(svc)
    assert svc.sweeps == 1, f"only the startup sweep, got {svc.sweeps}"


class _OlderService(_ReapingService):
    """Services that predate the id list return None instead of []."""

    async def reap_zombies(self):
        return None


def test_a_service_that_reports_no_ids_is_tolerated():
    svc = _OlderService()
    _run(svc)
    assert svc.sweeps == 1


class _SkippingThenIdle(_ReapingService):
    """The first sweep cannot judge anything (daemon still down), the rest can."""

    def __init__(self):
        super().__init__()
        self.skips = 0

    async def reap_zombies(self):
        return []

    async def recover_boundaries(self):
        self.sweeps += 1
        self.skips += 1
        # Two attempts while the daemon is still down, then it can judge.
        return {"skipped": "worker daemon unavailable"} if self.skips <= 2 else {"count": 0}


def test_a_skipped_sweep_does_not_idle_for_the_full_interval(monkeypatch):
    """W22: the startup sweep after an orchestrator restart runs while the daemon
    is still down. Spending the interval on that gives the orphan nothing."""
    from harness_orchestrator import worker as worker_mod

    monkeypatch.setattr(worker_mod, "_SWEEP_RETRY_SECONDS", 0.0)
    svc = _SkippingThenIdle()
    _run(svc)
    # Every skip retried; once a sweep actually judged (no skip), the interval resumed.
    assert svc.sweeps == 3, (
        f"two skips then one judging sweep, got {svc.sweeps} over {svc.polls} polls"
    )
