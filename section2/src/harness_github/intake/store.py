"""Lane-owned SQLite store for intake registrations, deliveries (idempotency) and intakes.

Deliberately NOT a shared-schema workaround: durable multi-instance state belongs in the shared
Postgres schema behind an A1 migration (NEEDS INTEGRATION, see the session report).
"""
# ruff: noqa: E501 (SQL literals)

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS registrations (
  id TEXT PRIMARY KEY, team_id TEXT NOT NULL, project_id TEXT NOT NULL, provider TEXT NOT NULL,
  external_project TEXT NOT NULL, secret_enc TEXT NOT NULL, api_token_enc TEXT, api_base TEXT,
  label TEXT NOT NULL DEFAULT 'harness', enabled INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL,
  created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS deliveries (
  registration_id TEXT NOT NULL, delivery_id TEXT NOT NULL, received_at REAL NOT NULL, outcome TEXT NOT NULL,
  PRIMARY KEY (registration_id, delivery_id));
CREATE TABLE IF NOT EXISTS intakes (
  id TEXT PRIMARY KEY, registration_id TEXT NOT NULL, team_id TEXT NOT NULL, project_id TEXT NOT NULL,
  provider TEXT NOT NULL, kind TEXT NOT NULL, external_id TEXT NOT NULL, url TEXT, title TEXT, body TEXT,
  actor TEXT, trigger TEXT, status TEXT NOT NULL DEFAULT 'pending_approval', decided_by TEXT,
  created_at REAL NOT NULL, decided_at REAL, UNIQUE (registration_id, kind, external_id));
CREATE TABLE IF NOT EXISTS hits (registration_id TEXT NOT NULL, ts REAL NOT NULL);
"""


class IntakeStore:
    def __init__(self, path: str | Path):
        p = Path(path)
        if str(path) != ":memory:":
            p.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._c = sqlite3.connect(str(path), check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        with self._lock:
            self._c.executescript(_SCHEMA)
            self._c.commit()

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._c.execute(sql, args).fetchall())

    def _x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._c.execute(sql, args)
            self._c.commit()
            return cur

    # -- registrations
    def add_registration(self, **kw) -> str:
        rid = uuid.uuid4().hex
        self._x(
            "INSERT INTO registrations (id, team_id, project_id, provider, external_project, secret_enc,"
            " api_token_enc, api_base, label, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                kw["team_id"],
                kw["project_id"],
                kw["provider"],
                kw["external_project"],
                kw["secret_enc"],
                kw.get("api_token_enc"),
                kw.get("api_base"),
                kw.get("label", "harness"),
                kw["created_by"],
                time.time(),
            ),
        )
        return rid

    def registration(self, rid: str) -> dict | None:
        rows = self._q("SELECT * FROM registrations WHERE id = ?", (rid,))
        return dict(rows[0]) if rows else None

    def registrations(self, team_id: str) -> list[dict]:
        return [
            dict(r)
            for r in self._q(
                "SELECT * FROM registrations WHERE team_id = ? ORDER BY created_at", (team_id,)
            )
        ]

    def disable_registration(self, rid: str) -> None:
        self._x("UPDATE registrations SET enabled = 0 WHERE id = ?", (rid,))

    # -- idempotency: True only for the first sighting of a delivery id
    def claim_delivery(self, rid: str, delivery_id: str, outcome: str = "received") -> bool:
        try:
            self._x(
                "INSERT INTO deliveries (registration_id, delivery_id, received_at, outcome) VALUES (?,?,?,?)",
                (rid, delivery_id, time.time(), outcome),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def set_delivery_outcome(self, rid: str, delivery_id: str, outcome: str) -> None:
        self._x(
            "UPDATE deliveries SET outcome = ? WHERE registration_id = ? AND delivery_id = ?",
            (outcome, rid, delivery_id),
        )

    def rate_hit(self, rid: str, limit: int, window_s: float, now: float | None = None) -> bool:
        """Sliding window; returns False (and does not record) when over the limit."""
        now = now if now is not None else time.time()
        with self._lock:
            self._c.execute("DELETE FROM hits WHERE ts < ?", (now - window_s,))
            n = self._c.execute(
                "SELECT COUNT(*) FROM hits WHERE registration_id = ?", (rid,)
            ).fetchone()[0]
            if n >= limit:
                self._c.commit()
                return False
            self._c.execute("INSERT INTO hits (registration_id, ts) VALUES (?,?)", (rid, now))
            self._c.commit()
            return True

    # -- intakes
    def add_intake(self, reg: dict, c) -> tuple[str, bool]:
        """(intake id, created). One intake per (registration, kind, external id): re-labelling
        an issue never creates a second one."""
        existing = self._q(
            "SELECT id FROM intakes WHERE registration_id = ? AND kind = ? AND external_id = ?",
            (reg["id"], c.kind, c.external_id),
        )
        if existing:
            return existing[0]["id"], False
        iid = uuid.uuid4().hex
        self._x(
            "INSERT INTO intakes (id, registration_id, team_id, project_id, provider, kind, external_id, url,"
            " title, body, actor, trigger, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                iid,
                reg["id"],
                reg["team_id"],
                reg["project_id"],
                c.provider,
                c.kind,
                c.external_id,
                c.url,
                c.title,
                c.body,
                c.actor,
                c.trigger,
                time.time(),
            ),
        )
        return iid, True

    def intake(self, iid: str) -> dict | None:
        rows = self._q("SELECT * FROM intakes WHERE id = ?", (iid,))
        return dict(rows[0]) if rows else None

    def intakes(self, team_id: str, status: str | None = None) -> list[dict]:
        if status:
            rows = self._q(
                "SELECT * FROM intakes WHERE team_id = ? AND status = ? ORDER BY created_at",
                (team_id, status),
            )
        else:
            rows = self._q(
                "SELECT * FROM intakes WHERE team_id = ? ORDER BY created_at", (team_id,)
            )
        return [dict(r) for r in rows]

    def decide(self, iid: str, status: str, user_id: str) -> bool:
        """Compare-and-set from pending_approval only; a decided intake never flips."""
        cur = self._x(
            "UPDATE intakes SET status = ?, decided_by = ?, decided_at = ? WHERE id = ? AND status = 'pending_approval'",
            (status, user_id, time.time(), iid),
        )
        return cur.rowcount == 1


def public(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.endswith("_enc")}
