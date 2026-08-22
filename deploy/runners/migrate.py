"""Idempotent numbered-migration runner (H6).

Single source of truth = section1/migrations/*.sql. Legacy databases that were
bootstrapped via Base.metadata.create_all are detected and marked as having 0001
applied, so the first migrate run only applies genuinely pending files.
"""

import asyncio
import re
import sys
from pathlib import Path

from sqlalchemy import text

HARNESS_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = HARNESS_ROOT / "section1" / "migrations"

MARK_LEGACY_SQL = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'projects')
       AND NOT EXISTS (SELECT 1 FROM schema_migrations)
    THEN
        INSERT INTO schema_migrations (version) VALUES ('0001');
    END IF;
END $$;
"""


def split_statements(sql: str) -> list[str]:
    """Semicolon-split that is dollar-quote ($$) aware and strips comment LINES
    (never whole statements) so leading comments can't eat a statement."""
    statements: list[str] = []
    buf: list[str] = []
    in_dollar = False
    i, n = 0, len(sql)
    while i < n:
        if sql.startswith("$$", i):
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        if not in_dollar and sql.startswith("--", i):
            while i < n and sql[i] != "\n":  # skip to end of comment line
                i += 1
            continue
        ch = sql[i]
        buf.append(ch)
        if ch == ";" and not in_dollar:
            statements.append("".join(buf))
            buf = []
        i += 1
    statements.append("".join(buf))

    out: list[str] = []
    for raw in statements:
        lines = [ln for ln in raw.splitlines() if not ln.strip().startswith("--")]
        stmt = "\n".join(lines).strip()
        if stmt:
            out.append(stmt)
    return out


async def migrate(engine) -> list[str]:
    applied: list[str] = []
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version VARCHAR(20) PRIMARY KEY, "
                "applied_at TIMESTAMPTZ DEFAULT now() NOT NULL)"
            )
        )
        await conn.execute(text(MARK_LEGACY_SQL))
        done = {
            row.version
            for row in (
                await conn.execute(text("SELECT version FROM schema_migrations"))
            ).fetchall()
        }
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = path.name.split("_", 1)[0]
            if version in done:
                continue
            for stmt in split_statements(path.read_text()):
                await conn.execute(text(stmt))
            await conn.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:v)"), {"v": version}
            )
            applied.append(path.name)
    return applied


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    async def _main() -> None:
        from services import get_factory

        factory = get_factory()
        engine = factory.kw["bind"]
        try:
            for name in await migrate(engine):
                print(f"applied {name}")
        finally:
            await engine.dispose()

    asyncio.run(_main())
