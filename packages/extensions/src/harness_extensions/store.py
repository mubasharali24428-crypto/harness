"""SQLite persistence for extensions, grants and the audit trail (one team-scoped schema).

ponytail: stdlib sqlite3, one connection + lock. Moving to the S1 Postgres/SQLAlchemy stack is
a migration owned by integration (NEEDS INTEGRATION); the audit table is append-only by API.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
create table if not exists extensions(
  id integer primary key, team_id text not null, kind text not null, name text not null,
  url text, tools text not null, enabled integer not null default 1,
  created_by text not null, created_at text not null, unique(team_id, name));
create table if not exists grants(
  id integer primary key, team_id text not null, extension_id integer not null references
  extensions(id), tool text not null, subject_kind text not null, subject text not null,
  created_by text not null, created_at text not null, revoked_at text);
create table if not exists audit(
  id integer primary key, ts text not null, team_id text not null, actor text not null,
  action text not null, target text not null, detail text not null);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: Path | str = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript(SCHEMA)

    def audit(self, team: str, actor: str, action: str, target: str, detail: dict | None = None):
        self.db.execute(
            "insert into audit(ts,team_id,actor,action,target,detail) values(?,?,?,?,?,?)",
            (now(), team, actor, action, target, json.dumps(detail or {})),
        )

    def audit_rows(self, team: str) -> list[sqlite3.Row]:
        q = "select * from audit where team_id=? order by id"
        return self.db.execute(q, (team,)).fetchall()
