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
from sqlalchemy.ext.asyncio import async_sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))  # events_retention, when loaded by path
from events_retention import ensure_forward_partitions

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


class SchemaDriftError(RuntimeError):
    """Applied migrations ⊄ declared on disk (M18-C6 drift gate). Refuses boot."""


def declared_versions() -> list[str]:
    """Version prefixes of every migration file on disk, in filename order.

    Shared by the apply loop and the drift gate so both read disk exactly the
    same way (`NNNN_description.sql` -> `NNNN`).
    """
    return [p.name.split("_", 1)[0] for p in sorted(MIGRATIONS_DIR.glob("*.sql"))]


async def check_drift(engine) -> None:
    """M18-C6 deploy-time schema-drift gate (OPS-16/OPS-24 residual).

    Refuses to continue when `schema_migrations` disagrees with the migration
    files shipped in THIS image:

      - applied > declared (a rolled-forward database meeting an old image:
        someone rolled back code without rolling back schema, or two images
        deployed against one DB): raise SchemaDriftError naming every extra
        version AND which on-disk tag matches it, so the operator can pick
        the right rollback image instead of guessing.
      - applied ⊄ declared (unknown/renamed versions): same refusal — the
        ledger no longer describes this image's chain.
      - applied < declared is NOT drift: that is just "migrations pending",
        and the normal migrate loop applies them below.

    Called by the compose app-tier pre-flight (entrypoint.sh) before any
    backend boots and at the top of migrate(); see docs/runbooks/rollback.md.

    M18-C4 fresh-install fix (found live: the loadtest probe's brand-new DB
    exited 37 here): an UNDEFINED schema_migrations table is not drift — it is
    "nothing applied yet", the normal state of every first boot. Only a table
    that EXISTS with unrecognized versions refuses. (The previous message told
    operators to "run the migrate service first" — circular on the very first
    deploy, since migrate() itself now calls this gate.)

    Portability fix (C6, same day): the existence probe originally used
    information_schema.tables, which is Postgres-only — section1's SQLite-
    backed test suite has no such schema. Inspector.has_table asks the
    dialect, so PG and SQLite behave identically.
    """
    async with engine.connect() as conn:
        from sqlalchemy import inspect

        ledger_exists = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).has_table("schema_migrations")
        )
        if not ledger_exists:
            return  # fresh database: nothing applied, nothing to disagree
        rows = (await conn.execute(text("SELECT version FROM schema_migrations"))).fetchall()

    applied = {str(row[0]) for row in rows}
    declared = set(declared_versions())
    extras = sorted(applied - declared)
    if not extras:
        return
    newest = max(extras)
    raise SchemaDriftError(
        f"SCHEMA DRIFT: database has {len(applied)} applied migrations but this "
        f"image declares only {len(declared)} (applied-but-unrecognized here: "
        f"{', '.join(extras)}). This image's code predates the schema it would "
        "talk to. ROLL BACK THE SCHEMA FIRST (restore a pre-deploy backup per "
        "docs/runbooks/backup-and-pitr.md) or roll FORWARD to an image whose "
        f"migrations include {newest} — i.e. any harness-backend tag built from "
        f"a commit containing section1/migrations/{newest}_*.sql. Never boot app "
        "servers against a mismatched schema; details: docs/runbooks/rollback.md."
    )


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
    # M18-C6 drift gate: refuse to touch the ledger if applied ⊄ declared
    # (e.g. rolled-forward DB vs old image). Applied < declared is fine —
    # that is just pending migrations, applied below.
    await check_drift(engine)
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
            done.add(version)
    # 0028 seeds agent_events partitions only through 2026-10 and inserts fail past the last one;
    # every deploy rolls them forward (the events-retention sidecar does it between deploys).
    if "0028" in done:  # a chain cut short before 0028 has no partitioned table yet
        await ensure_forward_partitions(async_sessionmaker(engine))
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
