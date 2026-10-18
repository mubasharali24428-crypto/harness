"""Team-scoped extension registry and default-deny grants. Every query is filtered by team."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .config import Config
from .netguard import validate_server_url
from .schema_guard import MAX_TOOLS, SchemaRejected, digest, validate_tool
from .store import Store, now

KINDS = {"mcp", "skill"}
SUBJECT_KINDS = {"role", "agent"}


class NotFound(LookupError):
    """Unknown id OR another team's id: indistinguishable on purpose (no existence oracle)."""


class Invalid(ValueError):
    pass


def _ext(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "kind": row["kind"], "name": row["name"], "url": row["url"],
        "enabled": bool(row["enabled"]), "tools": json.loads(row["tools"]),
        "createdBy": row["created_by"], "createdAt": row["created_at"],
    }  # fmt: skip


class Registry:
    def __init__(self, store: Store, cfg: Config):
        self.s, self.cfg = store, cfg

    def register(
        self, team: str, actor: str, *, kind: str, name: str, url: str | None, tools: list[Any]
    ) -> dict[str, Any]:
        if kind not in KINDS or not name or len(name) > 100:
            raise Invalid("kind must be mcp|skill and name 1-100 chars")
        if kind == "mcp":
            if not url:
                raise Invalid("mcp extensions need a url")
            url = validate_server_url(
                url, allow_loopback=self.cfg.allow_loopback, allow_private=self.cfg.allow_private
            )  # raises UrlRejected (a ValueError)
        elif url:
            raise Invalid("skills have no url")
        if len(tools) > MAX_TOOLS:
            raise SchemaRejected("too many tools")
        clean = []
        for t in tools:
            c = validate_tool(t)
            c["digest"] = digest(c)
            clean.append(c)
        if len({c["name"] for c in clean}) != len(clean):
            raise Invalid("duplicate tool names")
        with self.s.lock, self.s.db:
            try:
                cur = self.s.db.execute(
                    "insert into extensions(team_id,kind,name,url,tools,created_by,created_at)"
                    " values(?,?,?,?,?,?,?)",
                    (team, kind, name, url, json.dumps(clean), actor, now()),
                )
            except sqlite3.IntegrityError as exc:
                raise Invalid("an extension with this name already exists") from exc
            self.s.audit(team, actor, "extension.registered", str(cur.lastrowid), {"name": name})
            return self.get(team, cur.lastrowid)

    def list(self, team: str) -> list[dict[str, Any]]:
        rows = self.s.db.execute("select * from extensions where team_id=? order by id", (team,))
        return [_ext(r) for r in rows]

    def get(self, team: str, ext_id: int) -> dict[str, Any]:
        row = self.s.db.execute(
            "select * from extensions where id=? and team_id=?", (ext_id, team)
        ).fetchone()
        if row is None:
            raise NotFound(ext_id)
        return _ext(row)

    def disable(self, team: str, actor: str, ext_id: int) -> dict[str, Any]:
        self.get(team, ext_id)
        with self.s.lock, self.s.db:
            self.s.db.execute(
                "update extensions set enabled=0 where id=? and team_id=?", (ext_id, team)
            )
            self.s.audit(team, actor, "extension.disabled", str(ext_id))
        return self.get(team, ext_id)

    # --- grants: default deny, revocable, audited -------------------------------------------
    def grant(
        self, team: str, actor: str, ext_id: int, *, tool: str, subject_kind: str, subject: str
    ) -> dict[str, Any]:
        ext = self.get(team, ext_id)
        if subject_kind not in SUBJECT_KINDS or not subject:
            raise Invalid("subject_kind must be role|agent with a non-empty subject")
        if tool not in {t["name"] for t in ext["tools"]}:
            raise Invalid("tool is not defined by this extension")
        with self.s.lock, self.s.db:
            cur = self.s.db.execute(
                "insert into grants(team_id,extension_id,tool,subject_kind,subject,created_by,"
                "created_at) values(?,?,?,?,?,?,?)",
                (team, ext_id, tool, subject_kind, subject, actor, now()),
            )
            self.s.audit(
                team, actor, "grant.added", str(cur.lastrowid),
                {"extension": ext_id, "tool": tool, "subject": f"{subject_kind}:{subject}"},
            )  # fmt: skip
        return {"id": cur.lastrowid, "extensionId": ext_id, "tool": tool,
                "subjectKind": subject_kind, "subject": subject}  # fmt: skip

    def grants(self, team: str, ext_id: int) -> list[dict[str, Any]]:
        self.get(team, ext_id)
        rows = self.s.db.execute(
            "select * from grants where team_id=? and extension_id=? and revoked_at is null",
            (team, ext_id),
        )
        return [{"id": r["id"], "tool": r["tool"], "subjectKind": r["subject_kind"],
                 "subject": r["subject"]} for r in rows]  # fmt: skip

    def revoke(self, team: str, actor: str, ext_id: int, grant_id: int) -> None:
        self.get(team, ext_id)
        with self.s.lock, self.s.db:
            cur = self.s.db.execute(
                "update grants set revoked_at=? where id=? and team_id=? and extension_id=?"
                " and revoked_at is null",
                (now(), grant_id, team, ext_id),
            )
            if cur.rowcount == 0:
                raise NotFound(grant_id)
            self.s.audit(team, actor, "grant.revoked", str(grant_id), {"extension": ext_id})

    def authorize(
        self, team: str, actor: str, ext_id: int, tool: str, *, role: str, agent: str | None
    ) -> bool:
        """True only for an enabled extension with a live grant matching role or agent.

        Denials are audited; an unknown/foreign extension raises NotFound (never allowed).
        """
        ext = self.get(team, ext_id)
        subjects = [("role", role)] + ([("agent", agent)] if agent else [])
        allowed = False
        if ext["enabled"] and tool in {t["name"] for t in ext["tools"]}:
            for kind, subj in subjects:
                hit = self.s.db.execute(
                    "select 1 from grants where team_id=? and extension_id=? and tool=? and"
                    " subject_kind=? and subject=? and revoked_at is null",
                    (team, ext_id, tool, kind, subj),
                ).fetchone()
                allowed = allowed or hit is not None
        if not allowed:
            with self.s.lock, self.s.db:
                self.s.audit(team, actor, "call.denied", str(ext_id), {"tool": tool})
        return allowed
