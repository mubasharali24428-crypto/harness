"""Bootstrap dev database and freeze the v1 schema as SQL migration 0001."""

import asyncio
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from harness_identity.models import Base


async def main(database_url: str, dump_path: str | None) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if dump_path:
            def dump(sync_conn, path=dump_path):
                from sqlalchemy import create_engine as sync_create_engine
                from sqlalchemy.schema import CreateTable

                lines = ["-- migration 0001 initial schema\n"]
                for table in Base.metadata.sorted_tables:
                    ddl = str(CreateTable(table).compile(sync_create_engine("sqlite://")))
                    lines.append(ddl.rstrip(";\n") + ";\n")
                Path(path).write_text("\n".join(lines))

            # compile against the live dialect instead for accurate types
            def dump_pg(sync_conn):
                from sqlalchemy.dialects import postgresql
                from sqlalchemy.schema import CreateTable

                lines = ["-- migration 0001: initial frozen schema (17 tables)\n"]
                for table in Base.metadata.sorted_tables:
                    lines.append(
                        str(
                            CreateTable(table).compile(
                                dialect=postgresql.dialect()
                            )
                        ).strip()
                        + ";\n"
                    )
                Path(dump_path).write_text("\n".join(lines), encoding="utf-8")

            await conn.run_sync(lambda sc: dump_pg(sc))
            print(f"frozen schema written to {dump_path}")
    await engine.dispose()


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "postgresql+asyncpg://localhost/harness_dev"
    out = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(main(url, out))
