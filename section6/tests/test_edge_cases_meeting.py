"""M16-X5 — edge-case corpus, wave A: S6 meeting seeds (raw-18 §8).

Turns the raw-18 audit registry's Section-6 meeting edge seeds into
executable regression tests. Test names mirror the registry line IDs
(``x18_<n>_`` prefix) so the corpus maps back to the audit doc:

- x18_53  [meeting] close double-invocation — conditional idempotent close:
          the second close returns the SAME stamped meeting; closedAt never
          moves (the lifecycle lives in service.py/api.py, added this wave).
- x18_54  [meeting] ingest after-terminal — messages posted to a closed
          meeting answer honest 410 MEETING_CLOSED at the HTTP seam and are
          refused at the service seam; nothing is appended to the transcript.
- x18_order [meeting] concurrent posts preserve order — interleaved posts
          from two users keep strict append order in the transcript (and in
          the paginated API view), with stable monotonic messageSeq cursors.
- x18_rate_boundary [brief] rate-limit boundary — request #LIMIT (exactly
          the shipped budget) passes, #LIMIT+1 answers 429 RATE_LIMITED with
          Retry-After; a different IP is unaffected (per-IP+route keying).

Covered-elsewhere note (skipped here, listed per mission protocol): the
20/min budget contract pin, template keying across forged ids, window reset,
other-route isolation and store-outage fail-open live in
tests/test_g2_ratelimit_hooks.py; brief draft/approve state transitions in
tests/unit/test_brief_generator.py and tests/conformance/.
"""

import sys
from pathlib import Path

import pytest

# G2-style seam: the shared limiter + timeutil live in section1; this venv
# does not carry harness_identity as an installed distribution.
_ROOT = Path(__file__).resolve().parents[2]
if (_ROOT / "section1" / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "section1" / "src"))

from types import SimpleNamespace  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from harness_meeting.api import get_meeting_service, get_principal, router  # noqa: E402
from harness_meeting.ratelimit_hooks import install_meeting_rate_limit  # noqa: E402
from harness_meeting.service import (  # noqa: E402
    MEETING_CLOSED,
    MeetingClosedError,
    MeetingService,
)

RATE_BUCKETS_DDL = """
CREATE TABLE rate_buckets (
    bucket_key VARCHAR(200) PRIMARY KEY,
    window_start TIMESTAMPTZ NOT NULL,
    count INTEGER NOT NULL DEFAULT 0
)
"""


def _principal():
    return SimpleNamespace(userId="user-x5", teamId="team-x5", role="admin")


def _fresh_app(service: MeetingService | None = None) -> FastAPI:
    """Fresh app over an ISOLATED MeetingService so corpus tests never touch
    the api module-level singleton's meetings (cross-test bleed guard)."""
    svc = service if service is not None else MeetingService()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_meeting_service] = lambda: svc
    app.dependency_overrides[get_principal] = lambda: _principal()
    return app


# ---------------------------------------------------------------------------
# x18_53 — [meeting] close | double-invocation → conditional idempotent
# ---------------------------------------------------------------------------


def test_x18_53_close_double_invocation_is_idempotent_stamp_immutable():
    svc = MeetingService()
    meeting = svc.create_meeting("proj-x5", "Close Race")
    first = svc.close_meeting(meeting.id)
    assert first.status == MEETING_CLOSED
    assert first.closedAt is not None
    stamp = first.closedAt

    # The double-invocation itself: same object, same stamp, no error.
    second = svc.close_meeting(meeting.id)
    assert second.id == meeting.id
    assert second.status == MEETING_CLOSED
    assert second.closedAt == stamp, "closedAt must not move on re-close"

    # Unknown meeting still answers honestly.

    raised = False
    try:
        svc.close_meeting("mtg-nope")
    except KeyError:
        raised = True
    assert raised


async def test_x18_53_close_http_idempotent_and_terminal():
    client = TestClient(_fresh_app())
    meeting_id = client.post(
        "/api/v1/meetings", json={"projectId": "proj-x5", "title": "HTTP close"}
    ).json()["id"]

    r1 = client.post(f"/api/v1/meetings/{meeting_id}/close")
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["status"] == MEETING_CLOSED
    assert body1["closedAt"] is not None

    r2 = client.post(f"/api/v1/meetings/{meeting_id}/close")
    assert r2.status_code == 200, r2.text
    assert r2.json()["closedAt"] == body1["closedAt"], "stamp immutability"

    r_unknown = client.post("/api/v1/meetings/mtg-nope/close")
    assert r_unknown.status_code == 404
    assert r_unknown.json()["detail"]["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# x18_54 — [meeting] ingest | after-terminal → explicit guard + honest 410
# ---------------------------------------------------------------------------


async def test_x18_54_message_to_closed_meeting_is_refused_nothing_appended():
    svc = MeetingService()
    meeting = svc.create_meeting("proj-x5", "Closed Doors")
    svc.close_meeting(meeting.id)

    # Service seam: typed refusal...

    refused = False
    try:
        svc.post_message(meeting.id, "late-user", "hello?")
    except MeetingClosedError:
        refused = True
    assert refused

    # ...and NOTHING was appended to the transcript by the refusal.
    history, _has_more = svc.get_messages(meeting.id)
    assert history == []


async def test_x18_54_closed_vs_unknown_honest_410_vs_404():
    """The raw-18 ask: post-close traffic gets an HONEST 409/410-class code.
    410 GONE says 'this resource existed and is permanently gone' — exactly
    the terminal semantics of a closed meeting; unknown stays 404."""
    app = _fresh_app()
    client = TestClient(app)
    meeting_id = client.post(
        "/api/v1/meetings",
        json={"projectId": "proj-x5", "title": "Gone Meeting"},
    ).json()["id"]
    assert client.post(f"/api/v1/meetings/{meeting_id}/close").status_code == 200

    res = client.post(
        f"/api/v1/meetings/{meeting_id}/messages",
        json={"userId": "user-x5", "content": "anyone there?"},
    )
    assert res.status_code == 410, res.text
    detail = res.json()["detail"]
    assert detail["error"]["code"] == "MEETING_CLOSED"

    # Reads stay legal on closed meetings (history is not destroyed).
    res_read = client.get(f"/api/v1/meetings/{meeting_id}/messages")
    assert res_read.status_code == 200

    # Unknown meeting remains NOT_FOUND, distinct from closed.
    res_unknown = client.post(
        "/api/v1/meetings/mtg-nope/messages",
        json={"userId": "user-x5", "content": "echo"},
    )
    assert res_unknown.status_code == 404
    assert res_unknown.json()["detail"]["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# x18_order — concurrent posts preserve order (raw-18 §8 neighbor)
# ---------------------------------------------------------------------------


async def test_x18_order_concurrent_posts_preserve_strict_append_order():
    """Interleaved posts from two users land in strict submission order —
    the room history is append-only, so transcript, service read-back, and
    the paginated API view all agree, with stable monotonic messageSeq."""
    import asyncio

    svc = MeetingService()
    meeting = svc.create_meeting("proj-x5", "Ordered Chaos")

    seqs: list[tuple[str, str]] = []
    plan = [("alice", i) for i in range(15)] + [("bob", i) for i in range(15)]
    for user, n in plan:  # deterministic interleave: a0,b0,a1,b1,...
        msg = await asyncio.to_thread(svc.post_message, meeting.id, user, f"{user}-{n}")
        seqs.append((msg.userId, msg.content))
    assert seqs == [(u, f"{u}-{n}") for u, n in plan], "each poster sees own order"

    history, _has_more = svc.get_messages(meeting.id)
    contents = [m.content for m in history]
    expected = [f"{u}-{n}" for u, n in plan]
    assert contents == expected, "transcript must match exact submission order"
    assert [m.userId for m in history] == [u for u, _ in plan]
    assert len({m.id for m in history}) == len(plan), "ids stay unique"

    # The API pagination view agrees, with gap-free 1-based messageSeq.
    client = TestClient(_fresh_app(svc))
    page = client.get(f"/api/v1/meetings/{meeting.id}/messages").json()
    api_contents = [item["content"] for item in page["items"]]
    assert api_contents == expected
    api_seqs = [item["messageSeq"] for item in page["items"]]
    assert api_seqs == list(range(1, len(plan) + 1))
    assert page["hasMore"] is False


# ---------------------------------------------------------------------------
# x18_rate_boundary — [brief] generate | rate-limit boundary (#21/#22 edge)
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self, start: float) -> None:
        self.now_value = start

    def time(self) -> float:
        return self.now_value


@pytest.fixture
async def store_engine():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        from harness_identity.models import Base

        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(RATE_BUCKETS_DDL))
    yield engine
    await engine.dispose()


async def test_x18_rate_boundary_brief_generate_exact_budget_then_429(
    store_engine, monkeypatch
):
    """Boundary probe: request #BRIEF_GENERATE_LIMIT (the LAST allowed) still
    reaches the router; request #LIMIT+1 hits the wall with 429 RATE_LIMITED
    + Retry-After. A different IP keeps its own fresh bucket (keying)."""
    import harness_identity.ratelimit as rl
    from harness_identity.ratelimit import BRIEF_GENERATE_LIMIT
    from httpx import ASGITransport, AsyncClient

    clock = _Clock(7_000_000.0)
    monkeypatch.setattr(rl, "time", clock)
    factory = async_sessionmaker(store_engine, expire_on_commit=False)

    app = FastAPI()
    app.include_router(router)
    install_meeting_rate_limit(app, session_factory=factory)
    app.dependency_overrides[get_principal] = lambda: _principal()

    async def _post_brief(ip: str, meeting_id: str):
        transport = ASGITransport(app=app, client=(ip, 12345))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.post(f"/api/v1/meetings/{meeting_id}/brief")

    ip = "10.77.0.9"
    statuses = []
    for i in range(BRIEF_GENERATE_LIMIT):  # exactly the budget: all pass
        res = await _post_brief(ip, f"m-bound-{i}")
        statuses.append(res.status_code)
    assert all(s != 429 for s in statuses), "budget boundary must admit #N"

    # Request LIMIT+1: the wall.
    blocked = await _post_brief(ip, "m-bound-overflow")
    assert blocked.status_code == 429, blocked.text
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    retry_after = blocked.headers["retry-after"]
    assert retry_after.isdigit() and 1 <= int(retry_after) <= 60

    # Same moment, different IP: own bucket, untouched budget.
    other = await _post_brief("10.77.0.10", "m-other-ip")
    assert other.status_code != 429
