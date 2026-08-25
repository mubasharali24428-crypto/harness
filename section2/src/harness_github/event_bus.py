"""Local duck-typed event-bus publisher for Section 2's PR-open producer.

M17-B3: S2 previously instantiated section1's ``harness_identity.eventbus
.EventBus`` via a cross-section package import (invisible to the isolation
guard). This module reproduces EXACTLY the behavior the pr_open producer
relies on, with no section imports:

* ``publish(EventType, project_id, payload, run_id=...)`` INSERTs one row
  into the FROZEN ``agent_events`` table by name (seq from BIGSERIAL, ts
  from the column default — asyncpg rejects bound strings on TIMESTAMPTZ),
  then returns an envelope dict mirroring harness_contracts.EventEnvelope.
* The migration-0003 trigger ``trg_notify_agent_events`` turns every insert
  into ``pg_notify('agent_events', ...)`` — so cross-instance fan-out and
  WS delivery are unchanged; this process owns no subscriber queues (S2
  never consumed live deliveries).
* Best-effort by construction: failures raise into the caller exactly like
  EventBus.publish did (pr_open's emit_pr_opened already wraps publish in a
  blanket try/except and must never break the PR open that succeeded).

SQLite/aiosqlite compatible for tests: named binds and plain INSERT work on
both drivers. On SQLite there is no trigger-based notify, matching how the
S1 bus degrades in sqlite test mode ("direct fan-out covers same-instance
delivery" — and S2 has no in-process subscribers to cover).
"""

from __future__ import annotations

import logging
from typing import Any

from harness_contracts.events import EventType
from sqlalchemy import text

logger = logging.getLogger(__name__)

_INSERT_EVENT_SQL = text(
    "INSERT INTO agent_events (project_id, run_id, type, payload) "
    "VALUES (:project_id, :run_id, :type, :payload) RETURNING seq"
)


class LocalEventBus:
    """Persistence + pg_notify producer half of the shared event bus."""

    def __init__(self, session_factory) -> None:
        self._sessions = session_factory

    async def publish(
        self,
        event_type: EventType,
        project_id: str,
        payload: dict | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist one event row; the migration-0003 trigger notifies peers."""
        if run_id:
            await self.ensure_run(project_id, run_id)
        async with self._sessions() as session:
            seq = (
                await session.execute(
                    _INSERT_EVENT_SQL,
                    {
                        "project_id": project_id,
                        "run_id": run_id,
                        "type": event_type.value,
                        "payload": payload or {},
                    },
                )
            ).scalar_one()
            await session.commit()
        return {
            "type": event_type.value,
            "payload": payload or {},
            "ts": "",
            "seq": int(seq),
            "projectId": project_id,
            "runId": run_id,
        }

    async def ensure_run(self, project_id: str, run_id: str) -> None:
        """Idempotently create the agent_runs parent row for FK-safe publishing."""
        if not run_id:
            return
        async with self._sessions() as session:
            exists = await session.execute(
                text("SELECT 1 FROM agent_runs WHERE id = :id"), {"id": run_id}
            )
            if exists.first() is not None:
                return
            session.execute(
                text("INSERT INTO agent_runs (id, project_id) VALUES (:id, :pid)"),
                {"id": run_id, "pid": project_id},
            )
            try:
                await session.commit()
            except Exception:  # noqa: BLE001 — lost insert race: the row exists
                await session.rollback()


__all__ = ["LocalEventBus"]
