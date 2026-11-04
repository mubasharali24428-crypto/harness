"""Verify the audit_log hash chain built by migration 0036 (part 11.004).

The database seals every audit row at commit (``row_hash`` = SHA-256 over the
previous row's hash plus the row's own columns). This module re-derives every
hash with the SAME SQL function the trigger uses, so there is no second
canonicalisation to drift, and reports exactly what no longer matches:

* an edited row            -> ``row_hash`` differs from the recomputed value
* a deleted / inserted row -> the next row's ``prev_hash`` does not match, or
                              ``chain_pos`` has a gap
* deleted newest rows      -> ``audit_chain_head`` is ahead of the last row
* a row written around the trigger -> it has no ``chain_pos``

Retention may prune the OLDEST rows; the first surviving row then anchors the
chain (``pruned_prefix`` is reported) because the pruned part cannot be
re-verified. Postgres only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text

GENESIS = "0" * 64
BATCH = 5000

_ROWS = text(
    "SELECT chain_pos, seq, prev_hash, row_hash,"
    " audit_row_hash(prev_hash, seq, chain_pos, ts, actor_member_id, actor_kind,"
    "                action, target_type, target_id, detail) AS recomputed"
    " FROM audit_log WHERE chain_pos > :after ORDER BY chain_pos LIMIT :n"
)


@dataclass
class ChainReport:
    ok: bool = True
    rows_checked: int = 0
    first_pos: int | None = None
    last_pos: int | None = None
    pruned_prefix: bool = False
    problems: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.ok = False
        if len(self.problems) < 50:
            self.problems.append(msg)


_HEAD = text("SELECT last_pos, last_hash FROM audit_chain_head WHERE id = 1")
_PREDECESSOR = text("SELECT row_hash FROM audit_log WHERE chain_pos = :pos")
_FIRST = text("SELECT min(chain_pos) AS pos FROM audit_log WHERE chain_pos IS NOT NULL")
_LOOSE = text(
    "SELECT count(*) AS n, min(seq) AS first FROM audit_log WHERE chain_pos IS NULL"
)


async def verify_window(conn, *, after_chain_pos: int = 0, max_rows: int = BATCH) -> dict:
    """Verify one window of the chain and answer per row — the shape an export
    consumer needs, as opposed to `verify_chain`'s whole-ledger sweep.

    ``conn``: an AsyncConnection or AsyncSession on a PostgreSQL harness
    database. The recompute is done by the SAME trigger function that sealed the
    row, so there is no second canonicalisation that could drift and quietly
    pronounce a healthy chain broken (or a broken one healthy).

    Deliberately conservative about what it will not claim:

    * the first row of the window is linked against the row at
      ``chain_pos - 1`` if that row still exists; if retention pruned it the
      verdict says ``anchored`` and ``prunedPrefix``, because silence would let a
      customer believe more than is true;
    * ``windowComplete`` is only true when the window reaches the stored chain
      head, so a capped page never reads as "the whole trail is fine";
    * a head that disagrees with the last row is a problem, not a detail.
    """
    problems: list[str] = []
    rows = (
        (await conn.execute(_ROWS, {"after": after_chain_pos, "n": max_rows})).mappings().all()
    )
    surviving_first = (await conn.execute(_FIRST)).first()
    pruned_prefix = bool(surviving_first and surviving_first.pos and surviving_first.pos > 1)

    prev_hash: str | None = None
    prev_pos: int | None = None
    anchored = False  # the row before the window is gone: its link cannot be checked
    if rows:
        # Only the row immediately before the window can prove the window's first
        # link. A window that starts at position 5 because the customer asked for
        # it is not evidence that rows vanished; a missing predecessor is.
        first_pos = int(rows[0]["chain_pos"])
        if first_pos > 1:
            anchor = (
                await conn.execute(_PREDECESSOR, {"pos": first_pos - 1})
            ).scalar_one_or_none()
            if anchor is None:
                anchored = True
            else:
                prev_hash, prev_pos = anchor, first_pos - 1

    out = []
    for r in rows:
        pos = int(r["chain_pos"])
        intact = r["row_hash"] == r["recomputed"]
        if not intact:
            problems.append(f"chain_pos {pos} (seq {r['seq']}): row content was modified")
        if prev_pos is None:
            # Either the head of an unpruned chain (must be genesis) or the first
            # surviving row after a pruned prefix (nothing to link against).
            if pos == 1:
                linked = r["prev_hash"] == GENESIS
                if not linked:
                    problems.append(
                        f"chain_pos {pos} (seq {r['seq']}) does not start at the genesis hash"
                    )
            else:
                # Retention pruned the predecessor, so this window is anchored:
                # its first row is reported as intact and `prunedPrefix` /
                # `anchored` carry the caveat.
                linked = True
        elif pos != prev_pos + 1:
            linked = False
            problems.append(f"gap in chain: positions {prev_pos} -> {pos} (rows removed)")
        elif r["prev_hash"] != prev_hash:
            linked = False
            problems.append(f"chain_pos {pos} (seq {r['seq']}): prev_hash does not link")
        else:
            linked = True
        out.append(
            {
                "seq": int(r["seq"]),
                "chainPos": pos,
                "rowHash": r["row_hash"],
                "prevHash": r["prev_hash"],
                "ok": intact and linked,
            }
        )
        prev_hash, prev_pos = r["row_hash"], pos

    head = (await conn.execute(_HEAD)).first()
    window_complete = False
    head_out: dict | None = None
    if head is None:
        problems.append("audit_chain_head row is missing")
    else:
        head_out = {"chainPos": int(head.last_pos), "rowHash": head.last_hash}
        if rows:
            window_complete = prev_pos == int(head.last_pos)
            if window_complete and head.last_hash != prev_hash:
                problems.append(
                    "chain head hash does not match the last row (the head was altered"
                    " or rows were rewritten)"
                )
            elif (
                not window_complete
                and len(rows) < max_rows
                and int(head.last_pos) > (prev_pos or 0)
            ):
                # The window ran dry before the head while still under its cap, so
                # the rows between are not waiting to be read — they are gone. This
                # is the tail-deletion case a whole-ledger sweep catches, and a
                # customer who asked for "everything after N" deserves the same
                # answer here.
                problems.append(
                    f"chain head is at position {head.last_pos} but the last row is"
                    f" {prev_pos} (newest rows removed or the head was altered)"
                )
        elif after_chain_pos == 0 and int(head.last_pos) != 0:
            problems.append(
                f"audit_log is empty but the chain head is at {head.last_pos} (rows removed)"
            )

    loose = (await conn.execute(_LOOSE)).first()
    if loose is not None and loose.n:
        problems.append(f"{loose.n} audit row(s) are not in the chain (first seq {loose.first})")

    return {
        "ok": not problems,
        "rowsChecked": len(out),
        "firstChainPos": out[0]["chainPos"] if out else None,
        "lastChainPos": out[-1]["chainPos"] if out else None,
        "prunedPrefix": pruned_prefix,
        "anchored": anchored,
        "windowComplete": window_complete,
        "head": head_out,
        "rows": out,
        "problems": problems[:50],
    }


async def verify_chain(conn) -> ChainReport:
    """``conn``: an AsyncConnection or AsyncSession on the harness database."""
    rep = ChainReport()
    prev_hash: str | None = None
    prev_pos: int | None = None
    after = 0
    while True:
        rows = (await conn.execute(_ROWS, {"after": after, "n": BATCH})).mappings().all()
        if not rows:
            break
        for r in rows:
            pos = int(r["chain_pos"])
            if rep.first_pos is None:
                rep.first_pos = pos
                if pos != 1:
                    rep.pruned_prefix = True  # anchored at the first surviving row
                elif r["prev_hash"] != GENESIS:
                    rep.fail(f"chain_pos 1 (seq {r['seq']}) does not start at the genesis hash")
            else:
                if pos != prev_pos + 1:
                    rep.fail(f"gap in chain: positions {prev_pos} -> {pos} (rows removed)")
                if r["prev_hash"] != prev_hash:
                    rep.fail(f"chain_pos {pos} (seq {r['seq']}): prev_hash does not link")
            if r["row_hash"] != r["recomputed"]:
                rep.fail(f"chain_pos {pos} (seq {r['seq']}): row content was modified")
            prev_hash, prev_pos = r["row_hash"], pos
            rep.rows_checked += 1
            rep.last_pos = pos
        after = prev_pos or after

    head = (
        await conn.execute(text("SELECT last_pos, last_hash FROM audit_chain_head WHERE id = 1"))
    ).first()
    if head is None:
        rep.fail("audit_chain_head row is missing")
    elif rep.rows_checked == 0:
        if head.last_pos != 0:
            rep.fail(f"audit_log is empty but the chain head is at {head.last_pos} (rows removed)")
    elif head.last_pos != rep.last_pos or head.last_hash != prev_hash:
        rep.fail(
            f"chain head is at position {head.last_pos} but the last row is {rep.last_pos} "
            "(newest rows removed or the head was altered)"
        )

    loose = (
        await conn.execute(
            text("SELECT count(*) AS n, min(seq) AS first FROM audit_log WHERE chain_pos IS NULL")
        )
    ).first()
    if loose is not None and loose.n:
        rep.fail(f"{loose.n} audit row(s) are not in the chain (first seq {loose.first})")
    return rep
