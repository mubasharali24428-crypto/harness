"""Verify the audit_log hash chain (migration 0036).

    python section1/scripts/verify_audit_chain.py [DATABASE_URL]

DATABASE_URL defaults to $HARNESS_DATABASE_URL. Exit status: 0 chain intact,
1 tampering or breakage detected (problems are printed), 2 could not verify.
Read-only: it never writes to the database.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness_identity.audit_chain import verify_chain  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402


def _async_url(url: str) -> str:
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


async def main(url: str) -> int:
    engine = create_async_engine(_async_url(url))
    try:
        async with engine.connect() as conn:
            report = await verify_chain(conn)
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report and exit 2
        print(f"cannot verify: {exc}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()
    span = f"{report.first_pos}..{report.last_pos}" if report.rows_checked else "empty"
    note = " (older rows pruned by retention; chain anchored at first surviving row)" if (
        report.pruned_prefix
    ) else ""
    if report.ok:
        print(f"audit chain OK: {report.rows_checked} rows, positions {span}{note}")
        return 0
    print(f"audit chain BROKEN: {len(report.problems)} problem(s), {report.rows_checked} rows checked")
    for problem in report.problems:
        print(f"  - {problem}")
    return 1


if __name__ == "__main__":
    dsn = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("HARNESS_DATABASE_URL", "")
    if not dsn:
        print("usage: verify_audit_chain.py DATABASE_URL (or set HARNESS_DATABASE_URL)", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(dsn)))
