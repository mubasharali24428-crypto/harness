"""The SIEM push loop, as a service (Marathon 2.0 W16 slice 4).

Deliberately thin: every delivery law (claim, cursor, backoff, blocking) lives in
`siem_sink.push_due`, where the tests can reach it. This module only owns the
process — read the DSN, build the vault, loop, and never let one bad batch end
the loop. A missing worker means the collector silently stops receiving, which is
why compose runs it as its own service and the runbook says how to tell.
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .config import Settings
from .crypto import KeyVault
from .siem_sink import push_due

logger = logging.getLogger("harness_identity.siem_worker")


def _poll_seconds() -> float:
    try:
        raw = float(os.getenv("HARNESS_SIEM_POLL_SECONDS", "30"))
    except ValueError:
        raw = 30.0
    return max(5.0, min(raw, 300.0))


async def run() -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    vault = KeyVault(settings.master_key)
    poll = _poll_seconds()
    try:
        while True:
            try:
                result = await push_due(factory, vault=vault)
                if any(result.values()):
                    logger.info("siem push batch %s", result)
            except Exception:  # noqa: BLE001 — a store fault is not a reason to stop pushing
                logger.warning("siem push batch failed", exc_info=True)
            await asyncio.sleep(poll)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("HARNESS_LOG_LEVEL", "INFO"))
    asyncio.run(run())
