"""Long-running bounded retry worker for outbound integrations."""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .config import Settings
from .crypto import KeyVault
from .webhooks import deliver_due

logger = logging.getLogger("harness_identity.webhook_worker")


async def run() -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    vault = KeyVault(settings.master_key)
    poll_seconds = max(1.0, min(float(os.getenv("HARNESS_WEBHOOK_POLL_SECONDS", "5")), 60.0))
    try:
        while True:
            try:
                result = await deliver_due(factory, vault=vault)
                if any(result.values()):
                    logger.info("webhook delivery batch %s", result)
            except Exception:  # noqa: BLE001 — worker retries after transient store faults
                logger.warning("webhook delivery batch failed", exc_info=True)
            await asyncio.sleep(poll_seconds)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("HARNESS_LOG_LEVEL", "INFO"))
    asyncio.run(run())
