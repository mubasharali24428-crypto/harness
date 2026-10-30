"""One reader for team-scoped audit rows, so an export and a SIEM push cannot drift.

`app.py`'s export endpoint and `siem_sink.py`'s delivery loop must show a
collector EXACTLY what an auditor would export. That is only true if both call
this function rather than carrying their own copy of the scope rule and the row
projection — the scope rule in particular is security-relevant (rows bound to
the team's members, plus unbound denial rows whose detail names the team), and a
second copy of it would eventually disagree with the first.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Bound rows are the team's members' actions. Unbound rows (a denial against a
# non-member, an actor-less system event) are included when their detail names
# the team; LIKE is a conservative superset and CAST works on PostgreSQL jsonb
# and SQLite TEXT alike (M18-C8).
AUDIT_TEAM_SCOPE = (
    "(actor_member_id IN (SELECT id FROM team_members WHERE team_id = :team)"
    " OR (actor_member_id IS NULL AND CAST(detail AS TEXT) LIKE '%' || :needle || '%'))"
)

_COLUMNS = (
    "SELECT seq, actor_member_id, actor_kind, action, target_type, target_id,"
    " detail, ts, chain_pos, prev_hash, row_hash"
    " FROM audit_log WHERE seq > :after AND {scope} ORDER BY seq ASC"
)


def as_dict(value: Any) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def project(row: Any) -> dict[str, Any]:
    """The camelCase wire row — chain fields included, because a collector that
    cannot see prevHash/rowHash cannot verify what it was given."""
    return {
        "seq": row["seq"],
        "actorMemberId": row["actor_member_id"],
        "actorKind": row["actor_kind"],
        "action": row["action"],
        "targetType": row["target_type"],
        "targetId": row["target_id"],
        "detail": as_dict(row["detail"]),
        "ts": str(row["ts"]) if row["ts"] is not None else None,
        "chainPos": row["chain_pos"],
        "prevHash": row["prev_hash"],
        "rowHash": row["row_hash"],
    }


async def read_audit_page(
    db: AsyncSession, team_id: str, *, after_seq: int, limit: int
) -> tuple[list[dict[str, Any]], bool]:
    """Ascending rows after ``after_seq``, capped at ``limit``; plus ``has_more``.

    Fetches one row beyond the cap to answer `has_more` without a second query,
    which is the convention the export endpoint already used.
    """
    rows = (
        (
            await db.execute(
                text(_COLUMNS.format(scope=AUDIT_TEAM_SCOPE) + " LIMIT :n"),
                {
                    "after": after_seq,
                    "team": team_id,
                    "needle": f'"teamId": "{team_id}"',
                    "n": limit + 1,
                },
            )
        )
        .mappings()
        .all()
    )
    return [project(r) for r in rows[:limit]], len(rows) > limit


async def count_after(db: AsyncSession, team_id: str, *, after_seq: int) -> int:
    """How many rows this team still owes a collector past ``after_seq``."""
    return int(
        (
            await db.execute(
                text(
                    "SELECT count(*) FROM audit_log WHERE seq > :after AND "
                    + AUDIT_TEAM_SCOPE.format()
                ),
                {"after": after_seq, "team": team_id, "needle": f'"teamId": "{team_id}"'},
            )
        ).scalar_one()
    )


async def latest_seq(db: AsyncSession, team_id: str) -> int:
    """The newest seq this team owns, or 0 when it has no rows yet.

    A SIEM cursor is seeded from this, which is why it is a MAX over the scoped
    set rather than "the last row of the first page": a page cap would land the
    cursor in the middle of the trail, and a MAX taken without the scope rule
    would land it on another team's newest row — both silently skip evidence.
    """
    return int(
        (
            await db.execute(
                text(
                    "SELECT COALESCE(MAX(seq), 0) FROM audit_log WHERE "
                    + AUDIT_TEAM_SCOPE.format()
                ),
                {"team": team_id, "needle": f'"teamId": "{team_id}"'},
            )
        ).scalar_one()
    )
