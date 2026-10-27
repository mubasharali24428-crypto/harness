"""Chat session identity for `harness chat` (W6 slice 3).

The server has no conversation abstraction — a run is one goal, one gate, one
engine turn — so chat must say exactly what is remembered between turns and
where. Two answers, both explicit:

* the full transcript lives in a local session file under the harness config
  directory (same permission contract as credentials: 0700 dir, 0600 file,
  atomic replace);
* what the *engine* remembers is only the history block `compose_goal` renders
  into the next goal. Nothing is implied, cached server-side, or smuggled —
  if a turn is not in that block, the model never saw it.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config

HISTORY_HEADER = "Earlier turns in this conversation (oldest first):"
CURRENT_HEADER = "Current message:"

#: The history block is capped so a long chat cannot outgrow a reasonable goal.
#: When it must shrink, the OLDEST exchanges drop first.
MAX_HISTORY_CHARS = 4000


def session_path(session_id: str) -> Path:
    return config.config_dir() / "sessions" / f"{session_id}.json"


def _sessions_dir() -> Path:
    target = config.config_dir() / "sessions"
    target.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    os.chmod(target, 0o700)
    return target


def new_session(project_id: str | None = None, local_dir: str | None = None) -> ChatSession:
    sid = f"chat-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    _sessions_dir()
    created = ChatSession(session_id=sid, project_id=project_id, local_dir=local_dir)
    save_session(created)
    return created


def latest_session_id() -> str | None:
    directory = config.config_dir() / "sessions"
    if not directory.is_dir():
        return None
    candidates = sorted(directory.glob("chat-*.json"), key=lambda p: p.stat().st_mtime)
    return candidates[-1].stem if candidates else None


@dataclass
class ChatSession:
    """The transcript, as stored. `exchanges` is the whole memory between turns.

    `local_dir` makes a chat bound to a directory: every turn then executes
    through the confined local engine against that directory (slice 4), which
    is a property of the conversation, not of one invocation — resuming the
    session resumes the same workspace.
    """

    session_id: str
    project_id: str | None = None
    local_dir: str | None = None
    created_ts: float = field(default_factory=time.time)
    exchanges: list[dict[str, Any]] = field(default_factory=list)

    def add(self, message: str, goal: str, result: dict[str, Any]) -> None:
        # The transcript stores the PLAIN message (what the user typed); the
        # rendered goal is kept too, because it is exactly what the server saw.
        self.exchanges.append(
            {
                "ts": time.time(),
                "message": message,
                "goal": goal,
                "runId": result.get("runId"),
                "planId": result.get("planId"),
                "state": result.get("state"),
                "answer": result.get("answer", ""),
            }
        )

    def save(self) -> Path:
        return save_session(self)

    def compose_goal(self, message: str) -> str:
        return compose_goal(self, message)


def save_session(session: ChatSession) -> Path:
    target = session_path(session.session_id)
    _sessions_dir()
    payload = {
        "sessionId": session.session_id,
        "projectId": session.project_id,
        "localDir": session.local_dir,
        "createdTs": session.created_ts,
        "exchanges": session.exchanges,
    }
    fd, tmp = tempfile.mkstemp(prefix=".session-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def load_session(session_id: str) -> ChatSession | None:
    try:
        raw = json.loads(session_path(session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return ChatSession(
        session_id=str(raw.get("sessionId") or session_id),
        project_id=raw.get("projectId"),
        local_dir=raw.get("localDir"),
        created_ts=float(raw.get("createdTs") or 0.0),
        exchanges=list(raw.get("exchanges") or []),
    )


def history_block(session: ChatSession, *, budget: int = MAX_HISTORY_CHARS) -> str:
    """Render past exchanges newest-first until the budget runs out.

    Deterministic and inspectable: the bytes returned here are the ONLY thing
    earlier turns contribute to the next goal.
    """
    picked: list[str] = []
    used = 0
    for item in reversed(session.exchanges):
        answer = str(item.get("answer") or "").strip()
        message = str(item.get("message") or item.get("goal") or "").strip()
        line = f"- {message}\n  → {answer or item.get('state') or ''}"
        if used + len(line) > budget:
            break
        picked.append(line)
        used += len(line)
    if not picked:
        return ""
    return f"{HISTORY_HEADER}\n" + "\n".join(reversed(picked))


def compose_goal(session: ChatSession, message: str) -> str:
    block = history_block(session)
    if not block:
        return message
    return f"{block}\n\n{CURRENT_HEADER} {message}"
