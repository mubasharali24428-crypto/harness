"""Part 40 (session 12): the scheduled session purge.

A deleted session whose retention has elapsed is purged by the same rules as the admin route
(``POST /api/v1/sessions/{id}/purge`` in section1 ``sessions.py``): member rows erased, title and
description cleared, a tombstone kept, and the session's runs left alone (their links keep the run
gate refusing starts). The difference is the actor: a scheduled purge has no person behind it, so
its audit row is written with ``actor_kind = 'system'`` and no actor, and the detail says so.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text


async def purge_due(factory: Any, now: datetime | None = None) -> int:
    """Purge every deleted session past its retention that is not purged yet; returns the count."""
    moment = now or datetime.now(UTC)
    async with factory() as db:
        due = (
            await db.execute(
                text(
                    "SELECT id FROM collab_sessions"
                    " WHERE state = 'deleted' AND purged_at IS NULL AND purge_after <= :n"
                ),
                {"n": moment},
            )
        ).all()
        for (sid,) in due:
            removed = (
                await db.execute(
                    text("DELETE FROM session_members WHERE session_id = :s"), {"s": sid}
                )
            ).rowcount
            retained = (
                await db.execute(
                    text("SELECT COUNT(*) FROM session_runs WHERE session_id = :s"), {"s": sid}
                )
            ).scalar()
            await db.execute(
                text(
                    "UPDATE collab_sessions SET title = '[purged]', description = '',"
                    " leader_user_id = NULL, state_reason = NULL, purged_at = :n, updated_at = :n,"
                    " version = version + 1 WHERE id = :i"
                ),
                {"n": moment, "i": sid},
            )
            detail = {
                "membersErased": removed, "runsRetained": retained, "actor": "scheduler",
                "before": {"state": "deleted"}, "after": {"purged": True},
            }
            await db.execute(
                text(
                    "INSERT INTO audit_log (actor_member_id, actor_kind, action, target_type,"
                    " target_id, detail, ts) VALUES (NULL, 'system', 'session.purged', 'session',"
                    " :i, :d, :n)"
                ),
                {"i": sid, "d": json.dumps(detail), "n": moment},
            )
            await db.commit()
    return len(due)
