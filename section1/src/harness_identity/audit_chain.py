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
