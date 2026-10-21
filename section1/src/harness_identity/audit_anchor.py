"""Part 11 and 29 (session 12): an external anchor for the audit chain head.

The chain (migration 0036) is sealed by database triggers and checked by ``verify_chain``. A
principal that can write the table can edit a row and then rebuild every later row and the head
pointer, and ``verify_chain`` stays green. This module closes that gap: a scheduled job appends the
current head (``seq`` and ``row_hash``) to a sink the database role cannot write, and
``verify_anchors`` fails when an anchored row is gone or no longer hashes to its anchored value.

Sink: an append-only JSON-lines file, opened with O_APPEND and fsynced after each line. The
database role has no path to it. Limits (stated in evidence/s12/part11.md): the operating-system
user that runs the app can rewrite the file; rows written after the newest anchor and removed
before the next one are not covered; rows pruned by retention below the oldest surviving row are
indistinguishable from deletions of old anchored rows. Production should point the sink at storage
with a retention or object lock.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text


async def head_row(db: Any) -> dict[str, Any] | None:
    """The newest sealed row (the chain head), or None for an empty chain."""
    row = (
        await db.execute(
            text("SELECT seq, row_hash FROM audit_log WHERE row_hash IS NOT NULL"
                 " ORDER BY seq DESC LIMIT 1")
        )
    ).first()
    return None if row is None else {"seq": int(row[0]), "rowHash": row[1]}


def read_anchors(path: str) -> list[dict[str, Any]]:
    """Every anchor in the sink, oldest first; a missing sink is an empty list."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def append_anchor(path: str, anchor: dict[str, Any]) -> None:
    line = (json.dumps(anchor, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)


async def anchor_head(factory: Any, path: str, now: datetime | None = None) -> dict | None:
    """Append the current head unless the newest anchor already names it. Returns the anchor
    written, or None when the head has not moved."""
    async with factory() as db:
        head = await head_row(db)
    if head is None:
        return None
    anchors = read_anchors(path)
    if anchors and anchors[-1]["seq"] == head["seq"] and anchors[-1]["rowHash"] == head["rowHash"]:
        return None
    anchor = {**head, "anchoredAt": (now or datetime.now(UTC)).isoformat()}
    append_anchor(path, anchor)
    return anchor


async def verify_anchors(conn: Any, anchors: list[dict[str, Any]]) -> list[str]:
    """Problems found by checking every anchor against the live table (empty when clean)."""
    problems: list[str] = []
    oldest = (await conn.execute(text("SELECT min(seq) FROM audit_log"))).scalar()
    for anchor in anchors:
        row = (
            await conn.execute(
                text("SELECT row_hash FROM audit_log WHERE seq = :s"), {"s": anchor["seq"]}
            )
        ).first()
        if row is None:
            if oldest is not None and anchor["seq"] < oldest:
                continue  # pruned by retention, below the oldest surviving row
            problems.append(f"anchored row seq {anchor['seq']} is gone")
        elif row[0] != anchor["rowHash"]:
            problems.append(f"anchored row seq {anchor['seq']} was altered after anchoring")
    return problems
