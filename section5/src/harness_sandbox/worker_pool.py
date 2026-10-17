"""S4: multi-host worker pool - registration, fenced leases, lost-worker recovery.

State lives behind ``SqliteLeaseStore`` (WAL, ``BEGIN IMMEDIATE`` claims) so any number of
worker PROCESSES sharing the file coordinate correctly. True multi-HOST operation needs a
shared network store (Postgres ``SELECT ... FOR UPDATE SKIP LOCKED`` with the same
transitions); that adapter needs an S1 migration and is a NEEDS INTEGRATION item.

Safety properties: a job is leased to at most one worker at a time; each claim bumps a
fencing ``epoch`` and every renew/complete must present it, so a worker that lost its
lease (slow, partitioned) cannot overwrite the new owner's result; unhealthy or unknown
workers cannot claim; jobs exceeding ``max_attempts`` fail as ``lost`` instead of looping.
"""
from __future__ import annotations

import hmac
import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from pathlib import Path


class PoolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


SCHEMA = """
CREATE TABLE IF NOT EXISTS workers(id TEXT PRIMARY KEY, host TEXT, capacity INT, labels TEXT,
  last_seen REAL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, team_id TEXT, payload TEXT,
  state TEXT, owner TEXT, epoch INT DEFAULT 0, lease_expires REAL, attempts INT DEFAULT 0,
  result TEXT, created REAL);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
"""


class WorkerPool:
    def __init__(self, db: Path, join_secret: str, *, lease_seconds: float = 30,
                 heartbeat_timeout: float = 15, max_attempts: int = 3,
                 now: Callable[[], float] = time.time) -> None:
        self.db, self.secret = str(db), join_secret
        self.lease_seconds, self.hb_timeout, self.max_attempts = (
            lease_seconds, heartbeat_timeout, max_attempts)
        self.now = now
        c = self._conn()
        c.executescript(SCHEMA)
        c.close()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db, timeout=30, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        c.row_factory = sqlite3.Row
        return c

    def _tx(self):
        pool = self

        class _T:
            def __enter__(self):
                self.c = pool._conn()
                self.c.execute("BEGIN IMMEDIATE")
                return self.c

            def __exit__(self, exc_type, *_):
                self.c.execute("ROLLBACK" if exc_type else "COMMIT")
                self.c.close()

        return _T()

    # -- workers ------------------------------------------------------------------
    def register(self, host: str, join_token: str, capacity: int = 1,
                 labels: list[str] | None = None) -> str:
        if not hmac.compare_digest(join_token, self.secret):
            raise PoolError("JOIN_DENIED", "invalid join token")
        if capacity < 1:
            raise PoolError("BAD_REQUEST", "capacity must be >= 1")
        wid = f"wrk_{uuid.uuid4().hex[:12]}"
        with self._tx() as c:
            c.execute("INSERT INTO workers VALUES(?,?,?,?,?)",
                      (wid, host, capacity, json.dumps(labels or []), self.now()))
        return wid

    def heartbeat(self, worker_id: str) -> None:
        with self._tx() as c:
            if c.execute("UPDATE workers SET last_seen=? WHERE id=?",
                         (self.now(), worker_id)).rowcount == 0:
                raise PoolError("UNKNOWN_WORKER", "worker not registered")

    def healthy(self, worker_id: str) -> bool:
        with self._tx() as c:
            return self._healthy(c, worker_id)

    def _healthy(self, c, worker_id: str) -> bool:
        r = c.execute("SELECT last_seen FROM workers WHERE id=?", (worker_id,)).fetchone()
        return r is not None and self.now() - r["last_seen"] <= self.hb_timeout

    # -- jobs ---------------------------------------------------------------------
    def submit(self, team_id: str, payload: dict, job_id: str | None = None) -> str:
        jid = job_id or f"job_{uuid.uuid4().hex[:12]}"
        with self._tx() as c:
            c.execute("INSERT OR IGNORE INTO jobs(id,team_id,payload,state,created) "
                      "VALUES(?,?,?, 'queued', ?)", (jid, team_id, json.dumps(payload), self.now()))
        return jid

    def claim(self, worker_id: str) -> dict | None:
        with self._tx() as c:
            if not self._healthy(c, worker_id):
                raise PoolError("WORKER_UNHEALTHY", "register/heartbeat before claiming")
            w = c.execute("SELECT capacity FROM workers WHERE id=?", (worker_id,)).fetchone()
            running = c.execute("SELECT COUNT(*) n FROM jobs WHERE owner=? AND state='running'",
                                (worker_id,)).fetchone()["n"]
            if running >= w["capacity"]:
                return None
            job = c.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1"
                            ).fetchone()
            if job is None:
                return None
            epoch = job["epoch"] + 1
            c.execute("UPDATE jobs SET state='running', owner=?, epoch=?, lease_expires=?, "
                      "attempts=attempts+1 WHERE id=?",
                      (worker_id, epoch, self.now() + self.lease_seconds, job["id"]))
            return {"jobId": job["id"], "teamId": job["team_id"], "epoch": epoch,
                    "payload": json.loads(job["payload"]),
                    "leaseExpires": self.now() + self.lease_seconds}

    def _owned(self, c, job_id: str, worker_id: str, epoch: int):
        j = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if (j is None or j["state"] != "running" or j["owner"] != worker_id
                or j["epoch"] != epoch or self.now() >= j["lease_expires"]):
            raise PoolError("LEASE_LOST", "lease expired, reassigned, or not yours")
        return j

    def renew(self, job_id: str, worker_id: str, epoch: int) -> float:
        with self._tx() as c:
            self._owned(c, job_id, worker_id, epoch)
            exp = self.now() + self.lease_seconds
            c.execute("UPDATE jobs SET lease_expires=? WHERE id=?", (exp, job_id))
            return exp

    def complete(self, job_id: str, worker_id: str, epoch: int, result: dict) -> None:
        with self._tx() as c:
            self._owned(c, job_id, worker_id, epoch)
            c.execute("UPDATE jobs SET state='done', result=?, owner=NULL WHERE id=?",
                      (json.dumps(result), job_id))

    def reap(self) -> dict:
        """Requeue (or mark lost) jobs with an expired lease or a silent owner."""
        requeued, lost = [], []
        with self._tx() as c:
            for j in c.execute("SELECT * FROM jobs WHERE state='running'").fetchall():
                if self.now() < j["lease_expires"] and self._healthy(c, j["owner"]):
                    continue
                if j["attempts"] >= self.max_attempts:
                    c.execute("UPDATE jobs SET state='lost', owner=NULL WHERE id=?", (j["id"],))
                    lost.append(j["id"])
                else:
                    c.execute("UPDATE jobs SET state='queued', owner=NULL WHERE id=?", (j["id"],))
                    requeued.append(j["id"])
        return {"requeued": requeued, "lost": lost}

    def job(self, job_id: str) -> dict:
        with self._tx() as c:
            r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(r) if r else {}
