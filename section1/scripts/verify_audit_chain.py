"""Verify the audit_log hash chain (migration 0036) and, when a sink is given, every external
anchor of the chain head (part 11/29, harness_identity.audit_anchor).

    python section1/scripts/verify_audit_chain.py [--anchors PATH] [DATABASE_URL]

DATABASE_URL defaults to $HARNESS_DATABASE_URL; PATH defaults to $HARNESS_AUDIT_ANCHOR_PATH.
Exit status: 0 chain and anchors intact, 1 tampering or breakage detected (problems are printed),
2 could not verify. Read-only: it never writes to the database or the sink.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from harness_identity.audit_anchor import read_anchors, verify_anchors  # noqa: E402
from harness_identity.audit_chain import verify_chain  # noqa: E402


def _async_url(url: str) -> str:
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


async def main(url: str, anchors: str | None = None) -> int:
    engine = create_async_engine(_async_url(url))
    try:
        async with engine.connect() as conn:
            report = await verify_chain(conn)
            anchor_problems = await verify_anchors(conn, read_anchors(anchors)) if anchors else []
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report and exit 2
        print(f"cannot verify: {exc}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()
    span = f"{report.first_pos}..{report.last_pos}" if report.rows_checked else "empty"
    note = " (older rows pruned by retention; chain anchored at first surviving row)" if (
        report.pruned_prefix
    ) else ""
    if report.ok and not anchor_problems:
        checked = f"; {len(read_anchors(anchors))} anchors checked" if anchors else ""
        print(f"audit chain OK: {report.rows_checked} rows, positions {span}{note}{checked}")
        return 0
    problems = [*report.problems, *anchor_problems]
    print(f"audit chain BROKEN: {len(problems)} problem(s), {report.rows_checked} rows checked")
    for problem in problems:
        print(f"  - {problem}")
    return 1


if __name__ == "__main__":
    args = sys.argv[1:]
    sink = os.environ.get("HARNESS_AUDIT_ANCHOR_PATH") or None
    if "--anchors" in args:
        at = args.index("--anchors")
        sink = args[at + 1]
        del args[at:at + 2]
    dsn = args[0] if args else os.environ.get("HARNESS_DATABASE_URL", "")
    if not dsn:
        print("usage: verify_audit_chain.py [--anchors PATH] DATABASE_URL "
              "(or set HARNESS_DATABASE_URL)", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(dsn, sink)))
