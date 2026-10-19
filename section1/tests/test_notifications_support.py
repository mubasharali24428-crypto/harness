"""Shared helpers for the notification suites (Session 5 lane N). No tests live here.

Real migrations (0040 workspaces, 0041 sessions, 0046 notifications) on sqlite, the real routers,
a fake clock patched into the module, and a fake email sender. Rows that section4 ``notify.emit``
would write are inserted straight with SQL (``seed_item``): section1 never imports section4.
"""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity import notifications
from harness_identity.models import Base
from harness_identity.sessions import router as sessions_router
from harness_identity.workspaces import router as workspaces_router
from tests.test_sessions_support import (  # noqa: F401 - re-exported for the suites
    RUN_CLAIMS,
    add_member,
    create_session,
    err,
    migration_statements,
    seed_run,
)
from tests.test_workspaces_support import (  # noqa: F401 - re-exported for the suites
    audit_actions,
    bearer,
    create_ws,
    seed_project,
    seed_team,
)

N = "/api/v1/notifications"
T0 = datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self, t: datetime = T0) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


class FakeSender:
    """Stands in for NotifierDelivery: records every mail, fails on demand."""

    dev_mode = False

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[dict] = []

    async def deliver(self, to_email, subject, html_body, plain):
        self.sent.append({"to": to_email, "subject": subject, "html": html_body, "plain": plain})
        return (True, None) if self.ok else (False, "smtp down")


@asynccontextmanager
async def make_env(monkeypatch, clock: Clock | None = None):
    clock = clock or Clock()
    monkeypatch.setattr(notifications, "now_utc", clock)
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(RUN_CLAIMS))
        for mig in ("0040_workspaces.sql", "0041_sessions.sql", "0046_notifications.sql"):
            for stmt in migration_statements(mig):
                await conn.execute(text(stmt))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    for r in (notifications.router, sessions_router, workspaces_router):
        app.include_router(r)
    app.state.session_factory = factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, factory, clock
    await engine.dispose()


async def seed_item(factory, actor: dict, *, kind: str = "approval", code: str | None = None,
                    run_id: str | None = None, session_id: str | None = None,
                    workspace_id: str | None = None, urgent: bool = False, count: int = 1,
                    state: str = "unread", snoozed_until: datetime | None = None,
                    created: datetime = T0, source_kind: str = "approval",
                    source_id: str = "src-1", email_state: str = "none",
                    email_due_at: datetime | None = None, email_attempts: int = 0) -> str:
    nid = str(uuid4())
    code = code or notifications.SUMMARY_CODES[kind][0]
    delivery = {"inapp": "delivered", "email": {"state": email_state, "reason": None,
                                                "dueAt": None}}
    async with factory() as db:
        await db.execute(
            text("INSERT INTO notification_inbox (id, team_id, user_id, kind, source_kind,"
                 " source_id, run_id, session_id, workspace_id, urgent, group_key, count,"
                 " summary_code, state, snoozed_until, created_at, updated_at, delivery,"
                 " email_state, email_due_at, email_attempts) VALUES (:i,:t,:u,:k,:sk,:sid,:r,:s,"
                 ":w,:urg,:g,:c,:code,:st,:su,:cr,:cr,:d,:es,:due,:att)"),
            {"i": nid, "t": actor["teamId"], "u": actor["userId"], "k": kind, "sk": source_kind,
             "sid": source_id, "r": run_id, "s": session_id, "w": workspace_id, "urg": urgent,
             "g": f"{kind}:{nid}", "c": count, "code": code, "st": state, "su": snoozed_until,
             "cr": created, "d": json.dumps(delivery), "es": email_state, "due": email_due_at,
             "att": email_attempts})
        await db.commit()
    return nid


async def put_prefs(client, actor: dict, body: dict, path: str = "/preferences"):
    r = await client.put(f"{N}{path}", json=body, headers=bearer(actor))
    assert r.status_code == 200, r.text
    return r.json()


async def row(factory, nid: str) -> dict:
    async with factory() as db:
        return dict((await db.execute(text("SELECT * FROM notification_inbox WHERE id=:i"),
                                      {"i": nid})).mappings().first())
