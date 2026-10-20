"""Searchable organization-wide run archive (F117).

SQLite-backed store of runs + events, built from the frozen event stream and
cost rows. All text is scrubbed at ingest — raw goal text never persists.
Access control is applied by the caller (dashboard) via ``visible_teams``:
None means org-wide (admin), a set means those teams only, and an empty set
sees nothing. Retention is an explicit per-ingest policy stamp
(``retentionUntil`` per run), purged by ``purge_expired``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .events import CostEntry, RunEvent, scrub_text
from .metrics import build_records

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    team_id TEXT,
    outcome TEXT NOT NULL,
    plan_proposed_at TEXT,
    pr_opened_at TEXT,
    pr_id TEXT,
    cost_usd TEXT NOT NULL,
    goal TEXT NOT NULL,
    n_events INTEGER NOT NULL,
    ingested_at TEXT NOT NULL,
    retention_until TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    ts TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_runs_team ON runs (team_id);
CREATE INDEX IF NOT EXISTS idx_runs_outcome ON runs (outcome);
CREATE INDEX IF NOT EXISTS idx_runs_proposed ON runs (plan_proposed_at);
"""


@dataclass
class ArchiveReport:
    runs: int
    events: int


class Archive:
    def __init__(self, db_path: str) -> None:
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # -- ingest -------------------------------------------------------------

    def ingest(
        self,
        events: list[RunEvent],
        costs: list[CostEntry],
        project_team: dict[str, str],
        *,
        as_of: datetime | None = None,
        retention_days: int = 90,
        ingested_at: datetime | None = None,
    ) -> ArchiveReport:
        as_of = as_of or datetime.now(UTC)
        ingested_at = ingested_at or as_of
        retention_until = ingested_at + timedelta(days=retention_days)
        records = build_records(events, costs, project_team, as_of=as_of)

        events_by_run: dict[str, list[RunEvent]] = {}
        for ev in events:
            events_by_run.setdefault(ev.runId, []).append(ev)

        n_events = 0
        for rec in records:
            self._db.execute(
                "INSERT INTO runs (run_id, project_id, team_id, outcome, plan_proposed_at,"
                " pr_opened_at, pr_id, cost_usd, goal, n_events, ingested_at, retention_until)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(run_id) DO UPDATE SET outcome=excluded.outcome,"
                " cost_usd=excluded.cost_usd, goal=excluded.goal,"
                " n_events=excluded.n_events, ingested_at=excluded.ingested_at,"
                " retention_until=excluded.retention_until",
                (
                    rec.runId,
                    rec.projectId,
                    rec.teamId,
                    rec.outcome(as_of),
                    rec.planProposedAt.isoformat() if rec.planProposedAt else None,
                    rec.prOpenedAt.isoformat() if rec.prOpenedAt else None,
                    rec.prId,
                    str(rec.costUsd),
                    rec.goal,
                    len(events_by_run.get(rec.runId, [])),
                    ingested_at.isoformat(),
                    retention_until.isoformat(),
                ),
            )
            for ev in events_by_run.get(rec.runId, []):
                self._db.execute(
                    "INSERT OR REPLACE INTO events (run_id, seq, type, ts, payload_json)"
                    " VALUES (?,?,?,?,?)",
                    (
                        ev.runId,
                        ev.seq,
                        ev.type,
                        ev.ts.isoformat(),
                        json.dumps(_scrub_payload(ev.payload), sort_keys=True),
                    ),
                )
                n_events += 1
        self._db.commit()
        return ArchiveReport(runs=len(records), events=n_events)

    # -- search -------------------------------------------------------------

    def search(
        self,
        *,
        visible_teams: set[str] | None,
        q: str | None = None,
        team: str | None = None,
        outcome: str | None = None,
        from_: datetime | None = None,
        to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        clauses: list[str] = []
        params: list[object] = []
        if visible_teams is not None:
            if not visible_teams:
                return {"total": 0, "runs": []}
            placeholders = ",".join("?" for _ in visible_teams)
            clauses.append(f"team_id IN ({placeholders})")
            params.extend(sorted(visible_teams))
        if team is not None:
            clauses.append("team_id = ?")
            params.append(team)
        if outcome is not None:
            clauses.append("outcome = ?")
            params.append(outcome)
        if q:
            clauses.append(
                "(run_id LIKE ? OR pr_id LIKE ? OR goal LIKE ? OR project_id LIKE ?)"
            )
            like = f"%{scrub_text(q)}%"
            params.extend([like, like, like, like])
        if from_ is not None:
            clauses.append("plan_proposed_at >= ?")
            params.append(from_.isoformat())
        if to is not None:
            clauses.append("plan_proposed_at < ?")
            params.append(to.isoformat())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        total = self._db.execute(f"SELECT COUNT(*) FROM runs{where}", params).fetchone()[0]
        rows = self._db.execute(
            "SELECT run_id, project_id, team_id, outcome, plan_proposed_at, pr_opened_at,"
            f" pr_id, cost_usd, goal, n_events FROM runs{where}"
            " ORDER BY plan_proposed_at DESC, run_id LIMIT ? OFFSET ?",
            [*params, max(1, min(limit, 200)), max(0, offset)],
        ).fetchall()
        keys = (
            "runId", "projectId", "teamId", "outcome", "planProposedAt",
            "prOpenedAt", "prId", "costUsd", "goal", "eventCount",
        )
        return {"total": total, "runs": [dict(zip(keys, row, strict=True)) for row in rows]}

    def get_run(self, run_id: str, *, visible_teams: set[str] | None) -> dict | None:
        row = self._db.execute(
            "SELECT run_id, project_id, team_id, outcome, plan_proposed_at, pr_opened_at,"
            " pr_id, cost_usd, goal, n_events, ingested_at, retention_until"
            " FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        if visible_teams is not None and (row[2] or "") not in visible_teams:
            return None
        keys = (
            "runId", "projectId", "teamId", "outcome", "planProposedAt", "prOpenedAt",
            "prId", "costUsd", "goal", "eventCount", "ingestedAt", "retentionUntil",
        )
        run = dict(zip(keys, row, strict=True))
        events = self._db.execute(
            "SELECT seq, type, ts, payload_json FROM events WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
        run["events"] = [
            {"seq": s, "type": t, "ts": ts, "payload": json.loads(p)}
            for s, t, ts, p in events
        ]
        return run

    # -- retention ----------------------------------------------------------

    def purge_expired(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        cur = self._db.execute(
            "DELETE FROM runs WHERE retention_until < ?", (now.isoformat(),)
        )
        self._db.execute(
            "DELETE FROM events WHERE run_id NOT IN (SELECT run_id FROM runs)"
        )
        self._db.commit()
        return cur.rowcount


def _scrub_payload(payload: dict) -> dict:
    """Recursively scrub every string leaf before persisting a payload."""
    out: dict = {}
    for key, value in payload.items():
        if isinstance(value, str):
            out[key] = scrub_text(value)
        elif isinstance(value, dict):
            out[key] = _scrub_payload(value)
        elif isinstance(value, list):
            out[key] = [scrub_text(v) if isinstance(v, str) else v for v in value]
        else:
            out[key] = value
    return out
