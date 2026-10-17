"""Lane-owned SQLite persistence for the developer-workflow service.

This is the lane's own operational store (sessions, change sets, decisions,
comments, evidence). It is deliberately NOT a workaround for shared-schema
persistence: durable, project-scoped product state in the shared Postgres
schema remains a NEEDS INTEGRATION item with the Section 1 migration owner,
documented in the session report. Single-file SQLite keeps the component
independently runnable and honestly disposable.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS code_sessions (
    session_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    run_id TEXT,
    task_id TEXT,
    repo_ref TEXT,
    branch TEXT NOT NULL,
    forbidden_paths TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS change_sets (
    cs_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    title TEXT NOT NULL,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    applied_at TEXT,
    fingerprint TEXT
);

CREATE TABLE IF NOT EXISTS file_proposals (
    cs_id TEXT NOT NULL,
    path TEXT NOT NULL,
    base_sha256 TEXT,
    new_content TEXT NOT NULL,
    hunks TEXT NOT NULL,
    PRIMARY KEY (cs_id, path)
);

CREATE TABLE IF NOT EXISTS hunk_decisions (
    cs_id TEXT NOT NULL,
    path TEXT NOT NULL,
    hunk_id TEXT NOT NULL,
    decision TEXT NOT NULL DEFAULT 'pending',
    comment TEXT,
    decided_by TEXT,
    decided_at TEXT,
    PRIMARY KEY (cs_id, path, hunk_id)
);

CREATE TABLE IF NOT EXISTS line_comments (
    comment_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    side TEXT NOT NULL,
    body TEXT NOT NULL,
    anchor_sha256 TEXT NOT NULL,
    author TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verification_evidence (
    evidence_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    command TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    exit_code INTEGER NOT NULL DEFAULT 0,
    diagnostics TEXT NOT NULL DEFAULT '[]',
    tree_fingerprint TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS synced_files (
    session_id TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (session_id, path)
);

CREATE TABLE IF NOT EXISTS idempotency (
    scope TEXT NOT NULL,
    idem_key TEXT NOT NULL,
    response TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (scope, idem_key)
);
"""


class Store:
    """Small guarded SQLite wrapper. One connection, serialized access."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- generic helpers -----------------------------------------------------

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    # -- sessions ------------------------------------------------------------

    def create_session(
        self,
        *,
        session_id: str | None = None,
        project_id: str,
        team_id: str,
        run_id: str | None,
        task_id: str | None,
        repo_ref: str | None,
        branch: str,
        forbidden_paths: list[str],
        created_by: str,
    ) -> dict[str, Any]:
        session_id = session_id or new_id("dw")
        self._execute(
            "INSERT INTO code_sessions (session_id, project_id, team_id, run_id, task_id,"
            " repo_ref, branch, forbidden_paths, created_by, created_at, status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?, 'active')",
            (
                session_id,
                project_id,
                team_id,
                run_id,
                task_id,
                repo_ref,
                branch,
                json.dumps(forbidden_paths),
                created_by,
                now_iso(),
            ),
        )
        row = self.get_session(session_id)
        assert row is not None
        return row

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM code_sessions WHERE session_id = ?", (session_id,))
        if not rows:
            return None
        row = dict(rows[0])
        row["forbiddenPaths"] = json.loads(row.pop("forbidden_paths"))
        return row

    def list_sessions(self, team_id: str, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = self._query(
                "SELECT session_id FROM code_sessions WHERE team_id = ? AND project_id = ?"
                " ORDER BY created_at DESC",
                (team_id, project_id),
            )
        else:
            rows = self._query(
                "SELECT session_id FROM code_sessions WHERE team_id = ? ORDER BY created_at DESC",
                (team_id,),
            )
        return [self.get_session(r["session_id"]) for r in rows]  # type: ignore[misc]

    def close_session(self, session_id: str) -> None:
        self._execute(
            "UPDATE code_sessions SET status = 'closed' WHERE session_id = ?", (session_id,)
        )

    def onboarding_facts(self, team_id: str, user_id: str) -> dict[str, bool]:
        """Progress derived from what the user actually did, never self-asserted."""

        def has(sql: str) -> bool:
            return bool(self._query(sql, (team_id, user_id)))

        base = "FROM code_sessions s WHERE s.team_id = ? AND s.created_by = ?"
        return {
            "session": has(f"SELECT 1 {base} LIMIT 1"),
            "review": has(
                f"SELECT 1 {base} AND EXISTS (SELECT 1 FROM change_sets c WHERE c.session_id ="
                " s.session_id AND c.status = 'applied') LIMIT 1"
            ),
            "verify": has(
                f"SELECT 1 {base} AND EXISTS (SELECT 1 FROM verification_evidence v WHERE"
                " v.session_id = s.session_id) LIMIT 1"
            ),
        }

    # -- change sets ---------------------------------------------------------

    def create_change_set(
        self,
        session_id: str,
        title: str,
        note: str | None,
        created_by: str,
    ) -> str:
        cs_id = new_id("cs")
        self._execute(
            "INSERT INTO change_sets (cs_id, session_id, title, note, created_by, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (cs_id, session_id, title, note, created_by, now_iso()),
        )
        return cs_id

    def add_file_proposal(
        self,
        cs_id: str,
        path: str,
        base_sha256: str | None,
        new_content: str,
        hunks: list[dict[str, Any]],
    ) -> None:
        self._execute(
            "INSERT OR REPLACE INTO file_proposals"
            " (cs_id, path, base_sha256, new_content, hunks) VALUES (?,?,?,?,?)",
            (cs_id, path, base_sha256, new_content, json.dumps(hunks)),
        )

    def get_change_set(self, cs_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM change_sets WHERE cs_id = ?", (cs_id,))
        if not rows:
            return None
        cs = dict(rows[0])
        proposals = []
        for row in self._query(
            "SELECT * FROM file_proposals WHERE cs_id = ? ORDER BY path", (cs_id,)
        ):
            proposal = dict(row)
            proposal["hunks"] = json.loads(proposal["hunks"])
            decisions = {
                d["hunk_id"]: dict(d)
                for d in self._query(
                    "SELECT * FROM hunk_decisions WHERE cs_id = ? AND path = ?",
                    (cs_id, proposal["path"]),
                )
            }
            proposal["decisions"] = decisions
            proposals.append(proposal)
        cs["files"] = proposals
        return cs

    def set_decision(
        self,
        cs_id: str,
        path: str,
        hunk_id: str,
        decision: str,
        comment: str | None,
        decided_by: str,
    ) -> None:
        self._execute(
            "INSERT OR REPLACE INTO hunk_decisions"
            " (cs_id, path, hunk_id, decision, comment, decided_by, decided_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (cs_id, path, hunk_id, decision, comment, decided_by, now_iso()),
        )

    def mark_change_set_applied(self, cs_id: str, fingerprint: str) -> None:
        self._execute(
            "UPDATE change_sets SET status = 'applied', applied_at = ?, fingerprint = ?"
            " WHERE cs_id = ?",
            (now_iso(), fingerprint, cs_id),
        )

    def cancel_change_set(self, cs_id: str) -> None:
        self._execute("UPDATE change_sets SET status = 'cancelled' WHERE cs_id = ?", (cs_id,))

    def list_change_sets(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT cs_id FROM change_sets WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        )
        return [self.get_change_set(r["cs_id"]) for r in rows]  # type: ignore[misc]

    def latest_change_set(self, session_id: str) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT cs_id FROM change_sets WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        )
        if not rows:
            return None
        return self.get_change_set(rows[0]["cs_id"])

    # -- comments ------------------------------------------------------------

    def add_comment(
        self,
        *,
        session_id: str,
        path: str,
        start_line: int,
        end_line: int,
        side: str,
        body: str,
        anchor_sha256: str,
        author: str,
    ) -> dict[str, Any]:
        comment_id = new_id("cm")
        self._execute(
            "INSERT INTO line_comments (comment_id, session_id, path, start_line, end_line,"
            " side, body, anchor_sha256, author, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                comment_id,
                session_id,
                path,
                start_line,
                end_line,
                side,
                body,
                anchor_sha256,
                author,
                now_iso(),
            ),
        )
        return dict(
            self._query("SELECT * FROM line_comments WHERE comment_id = ?", (comment_id,))[0]
        )

    def list_comments(self, session_id: str, path: str | None = None) -> list[dict[str, Any]]:
        if path:
            rows = self._query(
                "SELECT * FROM line_comments WHERE session_id = ? AND path = ? ORDER BY created_at",
                (session_id, path),
            )
        else:
            rows = self._query(
                "SELECT * FROM line_comments WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            )
        return [dict(r) for r in rows]

    def get_comment(self, comment_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM line_comments WHERE comment_id = ?", (comment_id,))
        return dict(rows[0]) if rows else None

    def delete_comment(self, comment_id: str) -> None:
        self._execute("DELETE FROM line_comments WHERE comment_id = ?", (comment_id,))

    # -- evidence ------------------------------------------------------------

    def add_evidence(
        self,
        *,
        session_id: str,
        command: str,
        status: str,
        summary: str,
        exit_code: int,
        diagnostics: list[str],
        tree_fingerprint: str,
        created_by: str,
    ) -> dict[str, Any]:
        evidence_id = new_id("ev")
        self._execute(
            "INSERT INTO verification_evidence (evidence_id, session_id, command, status,"
            " summary, exit_code, diagnostics, tree_fingerprint, created_by, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                evidence_id,
                session_id,
                command,
                status,
                summary,
                exit_code,
                json.dumps(diagnostics),
                tree_fingerprint,
                created_by,
                now_iso(),
            ),
        )
        return dict(
            self._query(
                "SELECT * FROM verification_evidence WHERE evidence_id = ?", (evidence_id,)
            )[0]
        )

    def list_evidence(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM verification_evidence WHERE session_id = ? ORDER BY created_at DESC",
            (session_id,),
        )
        out = []
        for row in rows:
            item = dict(row)
            item["diagnostics"] = json.loads(item["diagnostics"])
            out.append(item)
        return out

    # -- sync bookkeeping ----------------------------------------------------

    def get_synced(self, session_id: str, path: str) -> str | None:
        rows = self._query(
            "SELECT sha256 FROM synced_files WHERE session_id = ? AND path = ?",
            (session_id, path),
        )
        return rows[0]["sha256"] if rows else None

    def set_synced(self, session_id: str, path: str, sha256: str) -> None:
        self._execute(
            "INSERT OR REPLACE INTO synced_files (session_id, path, sha256, synced_at)"
            " VALUES (?,?,?,?)",
            (session_id, path, sha256, now_iso()),
        )

    # -- idempotency ---------------------------------------------------------

    def get_idempotent(self, scope: str, key: str) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT response FROM idempotency WHERE scope = ? AND idem_key = ?",
            (scope, key),
        )
        return json.loads(rows[0]["response"]) if rows else None

    def put_idempotent(self, scope: str, key: str, response: dict[str, Any]) -> None:
        self._execute(
            "INSERT OR REPLACE INTO idempotency (scope, idem_key, response, created_at)"
            " VALUES (?,?,?,?)",
            (scope, key, json.dumps(response), now_iso()),
        )
