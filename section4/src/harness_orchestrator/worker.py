"""Single in-process worker loop that drains the durable run queue."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class RunQueueWorker:
    def __init__(self, service: Any, poll_interval: float = 0.5) -> None:
        self.service = service
        self.poll_interval = poll_interval
        self._task: asyncio.Task | None = None

    async def _loop(self) -> None:
        try:
            await self.service.recover_orphans()
        except Exception:  # noqa: BLE001 — startup recovery must not prevent serving
            logger.exception("orphan recovery failed at worker start")
        while True:
            try:
                await self.service.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one bad poll must never kill the worker
                logger.exception("run-queue poll failed")
            await asyncio.sleep(self.poll_interval)

    def run_forever(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
