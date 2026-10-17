"""S4: worker registration, fenced leases, lost-worker handling, cross-process exclusivity."""
import multiprocessing as mp

import pytest

from harness_sandbox.worker_pool import PoolError, WorkerPool

SECRET = "join-secret"


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture
def pool(tmp_path):
    c = Clock()
    return WorkerPool(tmp_path / "pool.db", SECRET, lease_seconds=10, heartbeat_timeout=5,
                      max_attempts=2, now=c), c


def test_join_token_and_health_gate(pool):
    p, c = pool
    with pytest.raises(PoolError) as e:
        p.register("h1", "wrong")
    assert e.value.code == "JOIN_DENIED"
    w = p.register("h1", SECRET)
    p.submit("t1", {"x": 1})
    c.t += 6                                              # heartbeat timeout passed
    with pytest.raises(PoolError) as e:
        p.claim(w)
    assert e.value.code == "WORKER_UNHEALTHY"
    p.heartbeat(w)
    assert p.claim(w)["payload"] == {"x": 1}
    with pytest.raises(PoolError):
        p.claim("wrk_unknown")


def test_capacity_and_fifo(pool):
    p, _ = pool
    w = p.register("h1", SECRET, capacity=1)
    a = p.submit("t1", {"n": 1})
    p.submit("t1", {"n": 2})
    first = p.claim(w)
    assert first["jobId"] == a
    assert p.claim(w) is None                             # at capacity
    p.complete(a, w, first["epoch"], {"ok": True})
    assert p.claim(w)["payload"] == {"n": 2}


def test_expired_lease_requeues_and_stale_owner_cannot_complete(pool):
    p, c = pool
    w1, w2 = p.register("h1", SECRET), p.register("h2", SECRET)
    jid = p.submit("t1", {})
    j1 = p.claim(w1)
    c.t += 11                                             # lease expires; w1 is silent
    p.heartbeat(w2)
    assert p.reap() == {"requeued": [jid], "lost": []}
    j2 = p.claim(w2)
    assert j2["epoch"] == j1["epoch"] + 1
    with pytest.raises(PoolError) as e:                   # zombie w1 wakes up
        p.complete(jid, w1, j1["epoch"], {"stale": True})
    assert e.value.code == "LEASE_LOST"
    with pytest.raises(PoolError):
        p.renew(jid, w1, j1["epoch"])
    p.complete(jid, w2, j2["epoch"], {"ok": True})
    assert p.job(jid)["state"] == "done" and '"ok"' in p.job(jid)["result"]


def test_renewal_keeps_a_job_and_silent_worker_loses_it(pool):
    p, c = pool
    w = p.register("h1", SECRET)
    jid = p.submit("t1", {})
    j = p.claim(w)
    c.t += 4
    p.heartbeat(w)
    p.renew(jid, w, j["epoch"])
    c.t += 4
    p.heartbeat(w)
    assert p.reap() == {"requeued": [], "lost": []}       # healthy + leased
    c.t += 6                                  # heartbeat gap > timeout, lease still valid
    assert p.reap()["requeued"] == [jid]


def test_attempts_are_bounded(pool):
    p, c = pool
    w = p.register("h1", SECRET)
    jid = p.submit("t1", {})
    for _ in range(2):
        p.heartbeat(w)
        p.claim(w)
        c.t += 11
        p.reap()
    assert p.job(jid)["state"] == "lost"


def _race(db, wid_secret, out):
    pool = WorkerPool(db, wid_secret)
    wid = pool.register("proc", wid_secret, capacity=1000)
    got = []
    while (j := pool.claim(wid)) is not None:
        got.append(j["jobId"])
    out.put(got)


def test_no_double_ownership_across_processes(tmp_path):
    db = tmp_path / "race.db"
    seed = WorkerPool(db, SECRET)
    ids = {seed.submit("t1", {"i": i}) for i in range(60)}
    q = mp.get_context("spawn").Queue()
    procs = [mp.get_context("spawn").Process(target=_race, args=(db, SECRET, q)) for _ in range(4)]
    [p.start() for p in procs]
    results = [q.get(timeout=60) for _ in procs]
    [p.join(30) for p in procs]
    claimed = [j for r in results for j in r]
    assert len(claimed) == len(set(claimed)) == 60        # each job claimed exactly once
    assert set(claimed) == ids
