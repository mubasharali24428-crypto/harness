"""A4-S5: prompt/workflow library (F135), run-linked feedback (F140), status probes (F139).

Same SQLite file as the lane store; tables are created here so S1-S4 schemas stay untouched."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from harness_devworkflow.guided import _CTRL, _SECRET
from harness_devworkflow.service import LaneError
from harness_devworkflow.store import new_id, now_iso

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,59}$")
_PARAM = re.compile(r"\{([a-z][a-z0-9_]{0,30})\}")
MAX_TEXT, MAX_STEPS, MAX_COMMENT = 4000, 20, 1000

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS prompt_library (
        lib_id TEXT PRIMARY KEY, team_id TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
        visibility TEXT NOT NULL, owner TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE (team_id, name))""",
    """CREATE TABLE IF NOT EXISTS prompt_versions (
        lib_id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
        created_by TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (lib_id, version))""",
    """CREATE TABLE IF NOT EXISTS feedback (
        feedback_id TEXT PRIMARY KEY, team_id TEXT NOT NULL, user_id TEXT NOT NULL,
        session_id TEXT NOT NULL, run_id TEXT NOT NULL, rating TEXT NOT NULL, comment TEXT NOT NULL,
        created_at TEXT NOT NULL, UNIQUE (team_id, user_id, session_id, run_id))""",
]


def _clean(text: str, limit: int, what: str) -> str:
    out = _CTRL.sub(" ", text).strip()
    if not out or len(out) > limit:
        raise LaneError("INVALID_REQUEST", f"{what} must be 1-{limit} characters", 422)
    return out


def _check_text(text: Any, what: str) -> str:
    if not isinstance(text, str):
        raise LaneError("INVALID_REQUEST", f"{what} must be a string", 422)
    out = _clean(text, MAX_TEXT, what)
    if _SECRET.search(out):
        raise LaneError("INVALID_REQUEST", f"{what} contains credential-looking text", 422)
    return out


def params_of(body: dict[str, Any]) -> list[str]:
    texts = [body["text"]] if "text" in body else [s["text"] for s in body["steps"]]
    return sorted({p for t in texts for p in _PARAM.findall(t)})


def validate_body(kind: str, body: Any) -> dict[str, Any]:
    if kind == "prompt" and isinstance(body, dict) and set(body) == {"text"}:
        return {"text": _check_text(body["text"], "text")}
    if kind == "workflow" and isinstance(body, dict) and set(body) == {"steps"}:
        steps = body["steps"]
        if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
            raise LaneError("INVALID_REQUEST", f"steps must hold 1-{MAX_STEPS} items", 422)
        out = []
        for s in steps:
            if not isinstance(s, dict) or set(s) != {"title", "text"}:
                raise LaneError("INVALID_REQUEST", "each step needs exactly title and text", 422)
            out.append(
                {
                    "title": _clean(str(s["title"]), 120, "step title"),
                    "text": _check_text(s["text"], "step text"),
                }
            )
        return {"steps": out}
    raise LaneError(
        "INVALID_REQUEST", "body does not match kind (prompt: {text}, workflow: {steps})", 422
    )


class Extras:
    def __init__(self, store, http: httpx.AsyncClient | None, bases: dict[str, str | None]):
        self.s, self.http, self.bases = store, http, bases
        for ddl in _SCHEMA:
            store._execute(ddl)

    # -- F135 library -----------------------------------------------------------------------
    def _entry(self, row, with_versions: bool = False) -> dict[str, Any]:
        d = dict(row)
        vers = self.s._query(
            "SELECT version, body, created_by, created_at FROM prompt_versions WHERE lib_id = ? ORDER BY version",
            (d["lib_id"],),
        )
        latest = json.loads(vers[-1]["body"])
        out = {
            "libId": d["lib_id"],
            "name": d["name"],
            "kind": d["kind"],
            "visibility": d["visibility"],
            "owner": d["owner"],
            "version": vers[-1]["version"],
            "params": params_of(latest),
        }
        if with_versions:
            out["versions"] = [
                {
                    "version": v["version"],
                    "createdBy": v["created_by"],
                    "createdAt": v["created_at"],
                    "body": json.loads(v["body"]),
                }
                for v in vers
            ]
        return out

    def _visible(self, row, p: dict[str, Any]) -> bool:
        return (
            row["visibility"] == "team" or row["owner"] == p["userId"] or p.get("role") == "admin"
        )

    def save(self, p: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name") or "")
        if not _NAME.match(name):
            raise LaneError(
                "INVALID_REQUEST", "name must be 1-60 chars: letters, digits, space, . _ -", 422
            )
        kind, vis = str(body.get("kind") or ""), str(body.get("visibility") or "private")
        if kind not in ("prompt", "workflow") or vis not in ("private", "team"):
            raise LaneError(
                "INVALID_REQUEST", "kind must be prompt|workflow, visibility private|team", 422
            )
        content = validate_body(kind, body.get("body"))
        rows = self.s._query(
            "SELECT * FROM prompt_library WHERE team_id = ? AND name = ?", (p["teamId"], name)
        )
        if rows:
            row = rows[0]
            if row["owner"] != p["userId"] and p.get("role") != "admin":
                raise LaneError("ROLE_DENIED", "only the owner or an admin can add a version", 403)
            if row["kind"] != kind:
                raise LaneError("INVALID_REQUEST", "kind of an existing entry cannot change", 409)
            lib_id = row["lib_id"]
            nxt = (
                self.s._query(
                    "SELECT MAX(version) AS v FROM prompt_versions WHERE lib_id = ?", (lib_id,)
                )[0]["v"]
                + 1
            )
            self.s._execute(
                "UPDATE prompt_library SET visibility = ? WHERE lib_id = ?", (vis, lib_id)
            )
        else:
            lib_id, nxt = new_id("lib"), 1
            self.s._execute(
                "INSERT INTO prompt_library VALUES (?,?,?,?,?,?,?)",
                (lib_id, p["teamId"], name, kind, vis, p["userId"], now_iso()),
            )
        self.s._execute(
            "INSERT INTO prompt_versions VALUES (?,?,?,?,?)",
            (lib_id, nxt, json.dumps(content), p["userId"], now_iso()),
        )
        return self._entry(
            self.s._query("SELECT * FROM prompt_library WHERE lib_id = ?", (lib_id,))[0], True
        )

    def _get(self, p, lib_id: str):
        rows = self.s._query(
            "SELECT * FROM prompt_library WHERE lib_id = ? AND team_id = ?", (lib_id, p["teamId"])
        )
        if not rows or not self._visible(rows[0], p):
            raise LaneError("LIBRARY_ENTRY_NOT_FOUND", "unknown library entry", 404)
        return rows[0]

    def list(self, p) -> list[dict[str, Any]]:
        rows = self.s._query(
            "SELECT * FROM prompt_library WHERE team_id = ? ORDER BY name", (p["teamId"],)
        )
        return [self._entry(r) for r in rows if self._visible(r, p)]

    def get(self, p, lib_id: str) -> dict[str, Any]:
        return self._entry(self._get(p, lib_id), True)

    def delete(self, p, lib_id: str) -> None:
        row = self._get(p, lib_id)
        if row["owner"] != p["userId"] and p.get("role") != "admin":
            raise LaneError("ROLE_DENIED", "only the owner or an admin can delete", 403)
        self.s._execute("DELETE FROM prompt_versions WHERE lib_id = ?", (lib_id,))
        self.s._execute("DELETE FROM prompt_library WHERE lib_id = ?", (lib_id,))

    def render(self, p, lib_id: str, values: Any, version: Any = None) -> dict[str, Any]:
        entry = self.get(p, lib_id)
        chosen = next(
            (v for v in entry["versions"] if v["version"] == (version or entry["version"])), None
        )
        if chosen is None or not isinstance(values, dict):
            raise LaneError("INVALID_REQUEST", "unknown version or values is not an object", 422)
        need = set(params_of(chosen["body"]))
        if set(values) != need:
            raise LaneError("INVALID_REQUEST", f"values must be exactly {sorted(need)}", 422)
        clean = {
            k: _clean(str(v) if isinstance(v, str) else "", 300, f"value '{k}'")
            for k, v in values.items()
        }
        fill = lambda t: _PARAM.sub(lambda m: clean[m.group(1)], t)
        b = chosen["body"]
        steps = (
            [{"title": s["title"], "text": fill(s["text"])} for s in b["steps"]]
            if "steps" in b
            else [{"title": entry["name"], "text": fill(b["text"])}]
        )
        return {"libId": lib_id, "version": chosen["version"], "steps": steps, "startsRun": False}

    # -- F140 feedback ----------------------------------------------------------------------
    def feedback(self, p, body: dict[str, Any]) -> dict[str, Any]:
        rating = body.get("rating")
        if rating not in ("up", "down"):
            raise LaneError("INVALID_REQUEST", "rating must be up|down", 422)
        sid, rid = body.get("sessionId") or None, body.get("runId") or None
        if not sid and not rid:
            raise LaneError(
                "INVALID_REQUEST", "feedback must be linked to a sessionId or runId", 422
            )
        sess = None
        if sid:
            sess = self.s.get_session(str(sid))
            if sess is None or sess["team_id"] != p["teamId"]:
                raise LaneError("SESSION_NOT_FOUND", "unknown code session", 404)
        if rid:
            known = [x for x in self.s.list_sessions(p["teamId"]) if x.get("run_id") == rid]
            if not known:
                # Runs live in the orchestrator; only runs this lane has seen can be linked here.
                raise LaneError("RUN_NOT_FOUND", "no run with that id is known to this lane", 404)
            if sess and sess.get("run_id") != rid:
                raise LaneError("INVALID_REQUEST", "runId does not belong to that session", 422)
        raw = body.get("comment") or ""
        if not isinstance(raw, str) or len(raw) > MAX_COMMENT:
            raise LaneError(
                "INVALID_REQUEST", f"comment must be at most {MAX_COMMENT} characters", 422
            )
        comment = _SECRET.sub("[REDACTED]", _CTRL.sub(" ", raw)).strip()
        fid = new_id("fb")
        self.s._execute(
            "INSERT INTO feedback VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (team_id, user_id, session_id, run_id)"
            " DO UPDATE SET rating = excluded.rating, comment = excluded.comment, created_at = excluded.created_at",
            (
                fid,
                p["teamId"],
                p["userId"],
                sid or "",
                rid or "",
                rating,
                comment,
                now_iso(),
            ),  # '' not NULL: UNIQUE must dedupe
        )
        return {"rating": rating, "sessionId": sid, "runId": rid, "comment": comment}

    def list_feedback(self, p, session_id: str | None, run_id: str | None) -> dict[str, Any]:
        sql, args = "SELECT * FROM feedback WHERE team_id = ?", [p["teamId"]]
        for col, v in (("session_id", session_id), ("run_id", run_id)):
            if v:
                sql, args = sql + f" AND {col} = ?", [*args, v]
        if p.get("role") not in ("admin", "engineer"):
            sql, args = sql + " AND user_id = ?", [*args, p["userId"]]
        rows = [
            {**dict(r), "session_id": r["session_id"] or None, "run_id": r["run_id"] or None}
            for r in self.s._query(sql + " ORDER BY created_at DESC", tuple(args))
        ]
        return {
            "items": rows,
            "up": sum(r["rating"] == "up" for r in rows),
            "down": sum(r["rating"] == "down" for r in rows),
        }

    # -- F139 status ------------------------------------------------------------------------
    async def status(self) -> dict[str, Any]:
        async def probe(name: str, base: str | None) -> dict[str, str]:
            if not base:
                return {"name": name, "state": "not_configured"}
            try:
                r = await self.http.get(base.rstrip("/") + "/health", timeout=3)  # type: ignore[union-attr]
            except httpx.HTTPError:
                return {"name": name, "state": "unreachable"}
            return {
                "name": name,
                "state": "operational" if r.status_code < 300 else "degraded",
                "detail": f"HTTP {r.status_code}",
            }

        comps = await asyncio.gather(*(probe(n, b) for n, b in self.bases.items()))
        return {
            "components": [{"name": "developer-workflow", "state": "operational"}, *comps],
            "checkedAt": now_iso(),
        }
