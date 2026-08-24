"""M15-W5-S4: one-shot re-encryption of stored vault payloads.

Re-wraps every ``api_keys.key_encrypted`` Fernet token under the current
salted-KDF derivation and stamps ``key_derivation_params.reencrypted_at`` to
mark the end of the dual-read transition window opened by migration 0022.

Idempotent: already-converted rows are re-wrapped harmlessly (fresh random IV,
same plaintext); safe to re-run after a partial run. The summary prints row
COUNTS only — secret material never enters logs or output.

Usage:
    ./.venv/bin/python scripts/reencrypt_vault.py [database_url]

    database_url defaults to $HARNESS_DATABASE_URL, then
    postgresql+asyncpg://localhost/harness_dev

Run AFTER applying migrations/0022_kdf_upgrade.sql and deploying the upgraded
code. Order: migrate -> deploy -> this script. Idempotent by design.
"""

import argparse
import asyncio
import os
import sys

# Make src/ importable when run from a bare checkout.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from harness_identity.crypto import reencrypt_all_payloads  # noqa: E402

DEFAULT_DB_URL = "postgresql+asyncpg://localhost/harness_dev"


async def main(database_url: str) -> int:
    from harness_identity.config import Settings
    from harness_identity.models import Base

    master_key = Settings().master_key  # env-provided; never printed
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)  # no-op on migrated installs
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            summary = await reencrypt_all_payloads(session, master_key)
        print(
            "reencrypt complete: "
            f"total={summary['total']} "
            f"rewrapped={summary['rewrapped']} "
            f"already_new={summary['already_new']} "
            f"failed={summary['failed']}"
        )
        return 1 if summary["failed"] else 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database_url", nargs="?", default=None)
    args = parser.parse_args()
    url = args.database_url or os.environ.get("HARNESS_DATABASE_URL") or DEFAULT_DB_URL
    raise SystemExit(asyncio.run(main(url)))
