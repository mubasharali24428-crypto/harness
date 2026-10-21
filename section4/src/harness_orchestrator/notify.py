"""In-product notifications: the producer side (master prompt part 47). See docs/notifications.md.

``emit`` is the ONE entry point other modules call (collab, service, discussion) to put a row in
a person's inbox. It applies the recipient's preferences, mutes, quiet hours, dedupe and
grouping, and writes ids plus a closed ``summary_code`` only: never comment, plan, diff or
session text (directive 47.006). It has no routes; the inbox API lives in section1
(``harness_identity/notifications.py``) and reads the rows written here (migration 0046).

Contract (the caller keeps its own transaction):

* runs in a SAVEPOINT inside the caller's session and never commits, so the row is atomic with
  the caller's change and a failure here cannot poison the caller's transaction;
* never raises (a broken notification must not break an approval or a handoff): the result is a
  small dict ``{"status": created|grouped|duplicate|suppressed|rejected|error, ...}``;
* same team only: the recipient must be a member of ``team_id`` and every scope id must belong
  to it.

The precedence and quiet-hours rules below are mirrored by section1 (sections may import only
``harness_contracts``); both sides are pinned by tests against the same documented table.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from harness_contracts.timeutil import as_utc, db_now
from sqlalchemy import text

from . import domain_metrics

logger = logging.getLogger(__name__)

# The closed set of kinds. Adding one needs a docs/notifications.md row and a summary code below.
KINDS = (
    "approval", "mention", "handoff", "failure", "blocked", "control_request", "suggestion",
    "budget",
)
# kind -> allowed summary codes, the first is the default. The code is the ONLY description of
# what happened that is stored; the preview text is derived from it at read time.
SUMMARY_CODES: dict[str, tuple[str, ...]] = {
    "approval": ("approval_requested", "approval_decided"),
    "mention": ("mentioned",),
    "handoff": ("handoff_offered", "handoff_accepted", "handoff_rejected"),
    "failure": ("run_failed", "review_failed"),
    "blocked": ("run_blocked",),
    "control_request": ("control_requested", "control_granted", "control_declined"),
    "suggestion": ("suggestion_queued",),
    "budget": ("budget_warning", "budget_exhausted", "plan_limit_reached"),
}
INAPP_MODES = ("on", "off")
EMAIL_MODES = ("off", "urgent", "immediate", "digest")
GROUP_SECONDS_ENV = "HARNESS_NOTIFY_GROUP_SECONDS"
DEFAULT_GROUP_SECONDS = 600
_MAX_ID = 80


class _Refused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def group_window() -> timedelta:
    try:
        secs = int(os.environ.get(GROUP_SECONDS_ENV, DEFAULT_GROUP_SECONDS))
        return timedelta(seconds=max(0, secs))
    except ValueError:
        return timedelta(seconds=DEFAULT_GROUP_SECONDS)


# ------------------------------------------------------------------ pure rules


def resolve(rows: list[dict[str, Any]], kind: str) -> tuple[str, str]:
    """(inapp, email) for one event. ``rows`` run from least to most specific (global, workspace,
    session). A more specific scope wins; inside one scope the per-event entry beats the scope
    default. Built-in defaults: in-app on, email off (email is opt-in only)."""
    inapp, email = "on", "off"
    for r in rows:
        inapp = r.get("inapp") or inapp
        email = r.get("email") or email
        ev = (r.get("events") or {}).get(kind) or {}
        inapp = ev.get("inapp") or inapp
        email = ev.get("email") or email
    return inapp, email


def _zone(name: str | None) -> ZoneInfo | Any:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001 - unknown or unavailable zone: fall back to UTC
        return UTC


def _minutes(hhmm: str | None) -> int | None:
    try:
        h, m = (hhmm or "").split(":")
        v = int(h) * 60 + int(m)
        return v if 0 <= v < 1440 else None
    except ValueError:
        return None


def quiet_until(
    now: datetime, tz_name: str | None, start: str | None, end: str | None
) -> datetime | None:
    """None when ``now`` is outside quiet hours, else the UTC instant they end.

    The window is wall-clock time in the user's own zone, start inclusive, end exclusive, and may
    wrap midnight (22:00-07:00). start == end or an unparsable value means no quiet hours."""
    s, e = _minutes(start), _minutes(end)
    if s is None or e is None or s == e:
        return None
    zone = _zone(tz_name)
    local = as_utc(now).astimezone(zone)
    cur = local.hour * 60 + local.minute
    inside = (s <= cur < e) if s < e else (cur >= s or cur < e)
    if not inside:
        return None
    day = local.date() + timedelta(days=1 if (s >= e and cur >= s) else 0)
    end_local = datetime.combine(day, time(e // 60, e % 60), tzinfo=zone)
    return end_local.astimezone(UTC)


# ------------------------------------------------------------------ storage helpers


def _rows_to_prefs(rows: Any) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        try:
            events = json.loads(r.events or "{}")
        except ValueError:
            events = {}
        out.append({
            "scope_kind": r.scope_kind, "inapp": r.inapp, "email": r.email,
            "events": events if isinstance(events, dict) else {},
            "timezone": r.timezone, "quiet_enabled": bool(r.quiet_enabled),
            "quiet_start": r.quiet_start, "quiet_end": r.quiet_end,
        })
    return out


async def _scope(db: Any, team_id: str, run_id: str | None, session_id: str | None,
                 workspace_id: str | None) -> tuple[str | None, str | None]:
    """Every scope id must belong to ``team_id``. Fills in the session and workspace a run
    lives in so per-session and per-workspace settings apply to run events too."""
    if run_id:
        row = (await db.execute(
            text("SELECT p.team_id AS team_id, p.workspace_id AS workspace_id FROM agent_runs r"
                 " JOIN projects p ON p.id = r.project_id WHERE r.id = :r"), {"r": run_id})).first()
        if row is None or row.team_id != team_id:
            raise _Refused("foreign_scope")
        workspace_id = workspace_id or row.workspace_id
        if session_id is None:
            link = (await db.execute(text("SELECT session_id FROM session_runs WHERE run_id = :r"),
                                     {"r": run_id})).first()
            session_id = link.session_id if link else None
    if session_id:
        row = (await db.execute(
            text("SELECT team_id, workspace_id FROM collab_sessions WHERE id = :s"),
            {"s": session_id})).first()
        if row is None or row.team_id != team_id:
            raise _Refused("foreign_scope")
        workspace_id = workspace_id or row.workspace_id
    if workspace_id:
        row = (await db.execute(text("SELECT team_id FROM workspaces WHERE id = :w"),
                                {"w": workspace_id})).first()
        if row is None or row.team_id != team_id:
            raise _Refused("foreign_scope")
    return session_id, workspace_id


async def _muted(db: Any, team_id: str, user_id: str, kind: str, run_id: str | None,
                 session_id: str | None, workspace_id: str | None, now: datetime) -> bool:
    clauses, prm = ["(scope_kind = 'kind' AND scope_id = :k)"], {"k": kind}
    for sk, val in (("run", run_id), ("session", session_id), ("workspace", workspace_id)):
        if val:
            clauses.append(f"(scope_kind = '{sk}' AND scope_id = :{sk})")
            prm[sk] = val
    row = (await db.execute(
        text("SELECT 1 FROM notification_mutes WHERE team_id = :t AND user_id = :u"
             " AND ended_at IS NULL AND (muted_until IS NULL OR muted_until > :now)"
             f" AND ({' OR '.join(clauses)}) LIMIT 1"),
        {"t": team_id, "u": user_id, "now": now, **prm})).first()
    return row is not None


# ------------------------------------------------------------------ emit


def _check(team_id: Any, user_id: Any, kind: Any, source_kind: Any, source_id: Any,
           run_id: Any, session_id: Any, workspace_id: Any, dedupe_key: Any) -> str | None:
    if kind not in KINDS:
        return "bad_kind"
    for v in (team_id, user_id, source_kind, source_id):
        if not isinstance(v, str) or not v or len(v) > _MAX_ID:
            return "bad_id"
    for v in (run_id, session_id, workspace_id):
        if v is not None and (not isinstance(v, str) or not v or len(v) > 36):
            return "bad_id"
    if len(source_kind) > 32:
        return "bad_id"
    if dedupe_key is not None and (not isinstance(dedupe_key, str) or not dedupe_key
                                   or len(dedupe_key) > 120):
        return "bad_dedupe_key"
    return None


def _counted(fn):
    """Count each emit result in ``harness_notifications_total{status}``."""
    @functools.wraps(fn)
    async def wrapper(*a: Any, **kw: Any) -> dict[str, Any]:
        out = await fn(*a, **kw)
        domain_metrics.inc("harness_notifications_total", str(out.get("status")))
        return out
    return wrapper


@_counted
async def emit(
    db: Any,
    *,
    team_id: str,
    user_id: str,
    kind: str,
    source_kind: str,
    source_id: str,
    run_id: str | None = None,
    session_id: str | None = None,
    workspace_id: str | None = None,
    urgent: bool = False,
    dedupe_key: str | None = None,
    summary_code: str | None = None,
) -> dict[str, Any]:
    """Put one notification in ``user_id``'s inbox, honouring their settings. Never raises."""
    try:
        bad = _check(team_id, user_id, kind, source_kind, source_id, run_id, session_id,
                     workspace_id, dedupe_key)
        if bad:
            return {"status": "rejected", "reason": bad}
        codes = SUMMARY_CODES[kind]
        code = summary_code if summary_code in codes else codes[0]
        async with db.begin_nested():
            return await _emit(db, team_id, user_id, kind, source_kind, source_id, run_id,
                               session_id, workspace_id, bool(urgent), dedupe_key, code)
    except _Refused as r:
        return {"status": "rejected", "reason": r.reason}
    except Exception:  # noqa: BLE001 - a notification problem must never break the caller
        logger.warning("notify.emit failed (kind=%s)", kind, exc_info=True)
        return {"status": "error"}


async def _emit(db: Any, team_id: str, user_id: str, kind: str, source_kind: str, source_id: str,
                run_id: str | None, session_id: str | None, workspace_id: str | None,
                urgent: bool, dedupe_key: str | None, code: str) -> dict[str, Any]:
    now = db_now()
    member = (await db.execute(
        text("SELECT 1 FROM team_members WHERE team_id = :t AND user_id = :u LIMIT 1"),
        {"t": team_id, "u": user_id})).first()
    if member is None:
        return {"status": "rejected", "reason": "not_in_team"}
    session_id, workspace_id = await _scope(db, team_id, run_id, session_id, workspace_id)

    scopes = ["(scope_kind = 'global')"]
    prm: dict[str, Any] = {"t": team_id, "u": user_id}
    if workspace_id:
        scopes.append("(scope_kind = 'workspace' AND scope_id = :w)")
        prm["w"] = workspace_id
    if session_id:
        scopes.append("(scope_kind = 'session' AND scope_id = :s)")
        prm["s"] = session_id
    order = {"global": 0, "workspace": 1, "session": 2}
    rows = sorted(
        _rows_to_prefs((await db.execute(
            text("SELECT * FROM notification_preferences WHERE team_id = :t AND user_id = :u"
                 f" AND ({' OR '.join(scopes)})"), prm)).all()),
        key=lambda r: order[r["scope_kind"]])
    inapp, email = resolve(rows, kind)
    if inapp == "off":
        return {"status": "suppressed", "reason": "inapp_off"}
    # Mute silences non-urgent events. Urgent ones still reach the inbox (never a popup or a sound).
    if not urgent and await _muted(db, team_id, user_id, kind, run_id, session_id,
                                   workspace_id, now):
        return {"status": "suppressed", "reason": "muted"}

    group_key = f"{kind}:{source_kind}:{run_id or session_id or workspace_id or source_id}:" \
                f"{'u' if urgent else 'n'}"
    if kind == "budget":  # a fresh unread warning must not swallow the hard-stop alert
        group_key += f":{code}"
    target = (await db.execute(
        text("SELECT id FROM notification_inbox WHERE team_id = :t AND user_id = :u"
             " AND group_key = :g AND state = 'unread' AND updated_at >= :since"
             " ORDER BY updated_at DESC LIMIT 1"),
        {"t": team_id, "u": user_id, "g": group_key, "since": now - group_window()})).first()
    inbox_id = target.id if target else str(uuid.uuid4())

    if dedupe_key:
        res = await db.execute(
            text("INSERT INTO notification_dedupe (team_id, user_id, dedupe_key, inbox_id,"
                 " created_at) VALUES (:t, :u, :k, :i, :n)"
                 " ON CONFLICT (team_id, user_id, dedupe_key) DO NOTHING"),
            {"t": team_id, "u": user_id, "k": dedupe_key, "i": inbox_id, "n": now})
        if res.rowcount != 1:
            return {"status": "duplicate"}

    if target:
        res = await db.execute(
            text("UPDATE notification_inbox SET count = count + 1, updated_at = :n,"
                 " source_id = :sid WHERE id = :i AND state = 'unread'"),
            {"n": now, "sid": source_id, "i": inbox_id})
        if res.rowcount == 1:
            return {"status": "grouped", "id": inbox_id}
        inbox_id = str(uuid.uuid4())  # the group row was read or dismissed meanwhile
        if dedupe_key:
            await db.execute(
                text("UPDATE notification_dedupe SET inbox_id = :i WHERE team_id = :t"
                     " AND user_id = :u AND dedupe_key = :k"),
                {"i": inbox_id, "t": team_id, "u": user_id, "k": dedupe_key})

    # Email: only when the user opted in. Quiet hours hold non-urgent mail, urgent bypasses them.
    email_state, reason, due = "none", "not_opted_in", None
    if email == "digest" and not urgent:
        email_state, reason = "digest", "digest"
    elif email == "urgent" and not urgent:
        reason = "urgent_only"
    elif email != "off":
        glob = next((r for r in rows if r["scope_kind"] == "global"), None)
        quiet = None
        if glob and glob["quiet_enabled"]:
            quiet = quiet_until(now, glob["timezone"], glob["quiet_start"], glob["quiet_end"])
        email_state, reason, due = "pending", "immediate", now
        if quiet:
            email_state, reason, due = (
                ("pending", "urgent_bypass", now) if urgent else ("pending", "quiet_hours", quiet))
    delivery = {"inapp": "delivered",
                "email": {"state": email_state, "reason": reason,
                          "dueAt": due.isoformat() if due else None}}
    await db.execute(
        text("INSERT INTO notification_inbox (id, team_id, user_id, kind, source_kind, source_id,"
             " run_id, session_id, workspace_id, urgent, dedupe_key, group_key, count,"
             " summary_code, state, created_at, updated_at, delivery, email_state, email_due_at,"
             " email_attempts) VALUES (:i, :t, :u, :k, :sk, :sid, :r, :s, :w, :urg, :dk, :g, 1,"
             " :c, 'unread', :n, :n, :d, :es, :due, 0)"),
        {"i": inbox_id, "t": team_id, "u": user_id, "k": kind, "sk": source_kind,
         "sid": source_id, "r": run_id, "s": session_id, "w": workspace_id, "urg": urgent,
         "dk": dedupe_key, "g": group_key, "c": code, "n": now, "d": json.dumps(delivery),
         "es": email_state, "due": due})
    return {"status": "created", "id": inbox_id}


RETENTION_DAYS_ENV = "HARNESS_NOTIFY_RETENTION_DAYS"
DEDUPE_DAYS_ENV = "HARNESS_NOTIFY_DEDUPE_DAYS"


def _days(env: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(env, default)))
    except ValueError:
        return default


async def purge_old(factory: Any, now: datetime | None = None, batch: int = 1000) -> dict[str, int]:
    """Retention for the notification tables. Deletes read and dismissed inbox rows untouched for
    ``HARNESS_NOTIFY_RETENTION_DAYS`` (default 90), dedupe rows older than
    ``HARNESS_NOTIFY_DEDUPE_DAYS`` (default 30, long past any replay window) and expired
    unsubscribe tokens. Unread and snoozed rows stay (a person has not dealt with them), and so
    does any row whose email is still waiting to go out. Idempotent; ``batch`` bounds each delete.
    Returns the rows deleted per table."""
    now = now or db_now()
    inbox_cut = now - timedelta(days=_days(RETENTION_DAYS_ENV, 90))
    dedupe_cut = now - timedelta(days=_days(DEDUPE_DAYS_ENV, 30))
    out = {"inbox": 0, "dedupe": 0, "unsub_tokens": 0}
    async with factory() as db:
        out["inbox"] = (await db.execute(
            text("DELETE FROM notification_inbox WHERE id IN (SELECT id FROM notification_inbox"
                 " WHERE state IN ('read', 'dismissed') AND updated_at < :cut"
                 " AND email_state NOT IN ('pending', 'digest') LIMIT :n)"),
            {"cut": inbox_cut, "n": batch})).rowcount or 0
        out["dedupe"] = (await db.execute(
            text("DELETE FROM notification_dedupe WHERE created_at < :cut"),
            {"cut": dedupe_cut})).rowcount or 0
        out["unsub_tokens"] = (await db.execute(
            text("DELETE FROM notification_unsub_tokens WHERE expires_at < :cut"),
            {"cut": now})).rowcount or 0
        await db.commit()
    for table, n in out.items():
        if n:
            domain_metrics.inc("harness_notification_purged_total", table, n)
    return out
