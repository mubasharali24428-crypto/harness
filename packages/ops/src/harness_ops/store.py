"""Shared SQLite state for ops: webhooks, deliveries, schedules, alert state.

One store so ops surfaces do not each grow their own persistence. All writes
go through a lock (check_same_thread=False); SQLite is deliberate at pilot
scale — see README for the "single-node" limit.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

_SCHEMA = """
CREATE TABLE IF NOT EXISTS webhooks (
    webhook_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    events_json TEXT NOT NULL,
    secret TEXT NOT NULL,
    team_id TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
    delivery_id TEXT PRIMARY KEY,
    webhook_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    response_code INTEGER,
    error TEXT,
    last_attempt_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedules (
    schedule_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    team_id TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    tz TEXT NOT NULL,
    goal_template TEXT NOT NULL,
    misfire_policy TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_run_at TEXT
);
CREATE TABLE IF NOT EXISTS schedule_runs (
    schedule_id TEXT NOT NULL,
    occurrence TEXT NOT NULL,
    triggered_at TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    catchup INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (schedule_id, occurrence)
);
CREATE TABLE IF NOT EXISTS alerts_state (
    alert_id TEXT PRIMARY KEY,
    rule TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    severity TEXT NOT NULL,
    subject_json TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    last_notified_at TEXT,
    count INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS approval_actions (
    token_hash TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    acted_at TEXT NOT NULL
);
"""


@dataclass
class WebhookRow:
    webhookId: str
    url: str
    events: list[str]
    secret: str
    teamId: str | None
    active: bool
    createdBy: str
    createdAt: str


@dataclass
class ScheduleRow:
    scheduleId: str
    name: str
    teamId: str
    spec: dict
    tz: str
    goalTemplate: str
    misfirePolicy: str
    enabled: bool
    createdBy: str
    createdAt: str
    lastRunAt: str | None


class OpsStore:
    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------- webhooks

    def insert_webhook(self, row: WebhookRow) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO webhooks VALUES (?,?,?,?,?,?,?,?)",
                (row.webhookId, row.url, json.dumps(row.events), row.secret,
                 row.teamId, int(row.active), row.createdBy, row.createdAt),
            )
            self._db.commit()

    def webhooks(self, *, active_only: bool = True) -> list[WebhookRow]:
        query = "SELECT * FROM webhooks"
        if active_only:
            query += " WHERE active = 1"
        rows = self._db.execute(query + " ORDER BY webhook_id").fetchall()
        return [
            WebhookRow(
                webhookId=r[0], url=r[1], events=json.loads(r[2]), secret=r[3],
                teamId=r[4], active=bool(r[5]), createdBy=r[6], createdAt=r[7],
            )
            for r in rows
        ]

    def set_webhook_active(self, webhook_id: str, active: bool) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE webhooks SET active = ? WHERE webhook_id = ?",
                (int(active), webhook_id),
            )
            self._db.commit()
            return cur.rowcount > 0

    # ----------------------------------------------------------- deliveries

    def record_delivery(
        self,
        *,
        delivery_id: str,
        webhook_id: str,
        event_type: str,
        attempt: int,
        status: str,
        response_code: int | None,
        error: str | None,
        at: datetime,
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO deliveries VALUES (?,?,?,?,?,?,?,?)",
                (delivery_id, webhook_id, event_type, attempt, status,
                 response_code, error, at.isoformat()),
            )
            self._db.commit()

    def delivery_history(self, webhook_id: str) -> list[dict]:
        rows = self._db.execute(
            "SELECT delivery_id, event_type, attempt, status, response_code, error,"
            " last_attempt_at FROM deliveries WHERE webhook_id = ?"
            " ORDER BY last_attempt_at, delivery_id",
            (webhook_id,),
        ).fetchall()
        return [
            {"deliveryId": r[0], "eventType": r[1], "attempt": r[2], "status": r[3],
             "responseCode": r[4], "error": r[5], "lastAttemptAt": r[6]}
            for r in rows
        ]

    # ------------------------------------------------------------ schedules

    def insert_schedule(self, row: ScheduleRow) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO schedules VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (row.scheduleId, row.name, row.teamId, json.dumps(row.spec), row.tz,
                 row.goalTemplate, row.misfirePolicy, int(row.enabled),
                 row.createdBy, row.createdAt, row.lastRunAt),
            )
            self._db.commit()

    def schedules(self, *, enabled_only: bool = False) -> list[ScheduleRow]:
        query = "SELECT * FROM schedules"
        if enabled_only:
            query += " WHERE enabled = 1"
        rows = self._db.execute(query + " ORDER BY schedule_id").fetchall()
        return [self._schedule_row(r) for r in rows]

    def schedule(self, schedule_id: str) -> ScheduleRow | None:
        row = self._db.execute(
            "SELECT * FROM schedules WHERE schedule_id = ?", (schedule_id,)
        ).fetchone()
        return self._schedule_row(row) if row else None

    @staticmethod
    def _schedule_row(row) -> ScheduleRow:
        return ScheduleRow(
            scheduleId=row[0], name=row[1], teamId=row[2], spec=json.loads(row[3]),
            tz=row[4], goalTemplate=row[5], misfirePolicy=row[6], enabled=bool(row[7]),
            createdBy=row[8], createdAt=row[9], lastRunAt=row[10],
        )

    def set_schedule_enabled(self, schedule_id: str, enabled: bool) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE schedules SET enabled = ? WHERE schedule_id = ?",
                (int(enabled), schedule_id),
            )
            self._db.commit()
            return cur.rowcount > 0

    def claim_schedule_run(
        self, *, schedule_id: str, occurrence: str, triggered_at: datetime,
        catchup: bool,
    ) -> bool:
        """Atomic claim for a (schedule, occurrence) slot, with a 5-minute
        lease: a crashed trigger's unfilled claim can be taken over after the
        grace window, so an outage never permanently swallows an occurrence.
        ``schedules.last_run_at`` only advances when a plan is actually
        attached, so release() restores a fully retryable state."""
        cutoff = (triggered_at - timedelta(minutes=5)).isoformat()
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO schedule_runs VALUES (?,?,?,'',?)",
                (schedule_id, occurrence, triggered_at.isoformat(), int(catchup)),
            )
            if cur.rowcount == 1:
                self._db.commit()
                return True
            took_over = self._db.execute(
                "UPDATE schedule_runs SET triggered_at = ?, catchup = ?"
                " WHERE schedule_id = ? AND occurrence = ? AND plan_id = ''"
                " AND triggered_at < ?",
                (triggered_at.isoformat(), int(catchup), schedule_id, occurrence,
                 cutoff),
            )
            self._db.commit()
            return took_over.rowcount == 1

    def attach_schedule_plan(self, schedule_id: str, occurrence: str, plan_id: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE schedule_runs SET plan_id = ? WHERE schedule_id = ?"
                " AND occurrence = ?",
                (plan_id, schedule_id, occurrence),
            )
            self._db.execute(
                "UPDATE schedules SET last_run_at = ? WHERE schedule_id = ? AND"
                " (last_run_at IS NULL OR last_run_at < ?)",
                (occurrence, schedule_id, occurrence),
            )
            self._db.commit()

    def release_schedule_run(self, schedule_id: str, occurrence: str) -> None:
        """Drop an unfilled claim after upstream failure so the next trigger
        sweep can retry the same occurrence."""
        with self._lock:
            self._db.execute(
                "DELETE FROM schedule_runs WHERE schedule_id = ? AND occurrence = ?"
                " AND plan_id = ''",
                (schedule_id, occurrence),
            )
            self._db.commit()

    def schedule_runs(self, schedule_id: str) -> list[dict]:
        rows = self._db.execute(
            "SELECT occurrence, triggered_at, plan_id, catchup FROM schedule_runs"
            " WHERE schedule_id = ? ORDER BY occurrence",
            (schedule_id,),
        ).fetchall()
        return [
            {"occurrence": r[0], "triggeredAt": r[1], "planId": r[2], "catchup": bool(r[3])}
            for r in rows
        ]

    # --------------------------------------------------------------- alerts

    def upsert_alert(
        self, *, alert_id: str, rule: str, dedupe_key: str, severity: str,
        subject: dict, message: str, now: datetime, notified: bool,
    ) -> None:
        with self._lock:
            self._db.execute(
                """INSERT INTO alerts_state
                       (alert_id, rule, dedupe_key, severity, subject_json, message,
                        status, first_seen, last_seen, last_notified_at, count)
                   VALUES (?,?,?,?,?,?, 'active', ?, ?, ?, 1)
                   ON CONFLICT(dedupe_key) DO UPDATE SET
                       last_seen = excluded.last_seen,
                       message = excluded.message,
                       severity = excluded.severity,
                       count = alerts_state.count + 1,
                       status = 'active',
                       last_notified_at = CASE WHEN ? THEN excluded.last_notified_at
                                               ELSE alerts_state.last_notified_at END""",
                (alert_id, rule, dedupe_key, severity, json.dumps(subject), message,
                 now.isoformat(), now.isoformat(), now.isoformat() if notified else None,
                 notified),
            )
            self._db.commit()

    def resolve_alert(self, dedupe_key: str, now: datetime) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT alert_id, rule, severity, subject_json, message, status,"
                " first_seen, last_seen, last_notified_at, count, dedupe_key"
                " FROM alerts_state WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
            if row is None or row[5] != "active":
                return None
            self._db.execute(
                "UPDATE alerts_state SET status = 'resolved', last_seen = ?,"
                " last_notified_at = ? WHERE dedupe_key = ?",
                (now.isoformat(), now.isoformat(), dedupe_key),
            )
            self._db.commit()
            return self._alert_row(row)

    def active_alert(self, dedupe_key: str) -> dict | None:
        row = self._db.execute(
            "SELECT alert_id, rule, severity, subject_json, message, status, first_seen,"
            " last_seen, last_notified_at, count, dedupe_key FROM alerts_state"
            " WHERE dedupe_key = ?",
            (dedupe_key,),
        ).fetchone()
        return self._alert_row(row) if row and row[5] == "active" else None

    def alerts(self, *, status: str | None = None) -> list[dict]:
        query = "SELECT alert_id, rule, severity, subject_json, message, status,"\
                " first_seen, last_seen, last_notified_at, count, dedupe_key"\
                " FROM alerts_state"
        params: tuple = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        rows = self._db.execute(query + " ORDER BY last_seen, alert_id", params).fetchall()
        return [self._alert_row(r) for r in rows]

    @staticmethod
    def _alert_row(row) -> dict:
        return {
            "alertId": row[0], "rule": row[1], "severity": row[2],
            "subject": json.loads(row[3]), "message": row[4], "status": row[5],
            "firstSeen": row[6], "lastSeen": row[7], "lastNotifiedAt": row[8],
            "count": row[9], "dedupeKey": row[10],
        }

    # ------------------------------------------------------ approval actions

    def record_approval_action(
        self, *, token_hash: str, actor: str, plan_id: str, decision: str,
        outcome: dict, at: datetime,
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO approval_actions VALUES (?,?,?,?,?,?)",
                (token_hash, actor, plan_id, decision, json.dumps(outcome), at.isoformat()),
            )
            self._db.commit()

    def approval_action(self, token_hash: str) -> dict | None:
        row = self._db.execute(
            "SELECT token_hash, actor, plan_id, decision, outcome_json, acted_at"
            " FROM approval_actions WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        if row is None:
            return None
        return {
            "tokenHash": row[0], "actor": row[1], "planId": row[2], "decision": row[3],
            "outcome": json.loads(row[4]), "actedAt": row[5],
        }
