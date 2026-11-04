"""W15: rotate HARNESS_MASTER_KEY and re-wrap every stored secret.

Different operation from ``reencrypt_vault.py`` (same key, SHA-256 → salted
derivation). This one changes the key itself: every payload is decrypted with
the OLD key's read set (salted, then legacy) and re-encrypted under the NEW
key's salted derivation. The cleartext KDF salt row is reused, so nothing is
lost by construction; a row the old key cannot read is counted ``failed`` and
left untouched.

Keys are read from ENVIRONMENT VARIABLES, named by flag — never from argv,
where the process list would expose them:

    HARNESS_OLD_MASTER_KEY=... HARNESS_NEW_MASTER_KEY=... \
        ./.venv/bin/python scripts/rotate_master_key.py --dry-run
    HARNESS_OLD_MASTER_KEY=... HARNESS_NEW_MASTER_KEY=... \
        ./.venv/bin/python scripts/rotate_master_key.py

Exit code is 1 when any row failed to decrypt, so a pipeline cannot mistake a
partial rotation for success. Output is COUNTS ONLY; secret material never
enters logs.

Deploy order (see docs/runbooks/secret-rotation.md): run this while the old
key is still the deployed one, then deploy the new key. Any write that lands
between the two steps is undecryptable by the new key; re-running the script
with the old key reports it as ``failed``.
"""

import argparse
import asyncio
import json
import os
import sys

# Make src/ importable when run from a bare checkout.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from harness_identity.crypto import rotate_master_key  # noqa: E402

DEFAULT_DB_URL = "postgresql+asyncpg://localhost/harness_dev"


async def main(database_url: str, old_env: str, new_env: str, dry_run: bool) -> int:
    from harness_identity.models import Base

    old_key = os.environ.get(old_env, "").encode()
    new_key = os.environ.get(new_env, "").encode()
    if not old_key or not new_key:
        print(f"refusing: set {old_env} and {new_env} (values are never printed)")
        return 2

    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)  # no-op on migrated installs
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            summary = await rotate_master_key(session, old_key, new_key, dry_run=dry_run)
        print(json.dumps(summary, sort_keys=True))
        if summary["failed"]:
            print(
                f"INCOMPLETE: {summary['failed']} row(s) could not be read with "
                f"{old_env}; they were left unchanged",
                file=sys.stderr,
            )
        return 1 if summary["failed"] else 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rotate the vault master key.")
    parser.add_argument("database_url", nargs="?", default=None)
    parser.add_argument("--old-key-env", default="HARNESS_OLD_MASTER_KEY")
    parser.add_argument("--new-key-env", default="HARNESS_NEW_MASTER_KEY")
    parser.add_argument(
        "--dry-run", action="store_true", help="decrypt-only; writes nothing"
    )
    args = parser.parse_args()
    url = args.database_url or os.environ.get("HARNESS_DATABASE_URL") or DEFAULT_DB_URL
    raise SystemExit(
        asyncio.run(main(url, args.old_key_env, args.new_key_env, args.dry_run))
    )
