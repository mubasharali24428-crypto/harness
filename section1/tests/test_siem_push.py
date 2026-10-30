"""W16 slice 4 — the SIEM delivery loop.

`push_due` is the part a customer cannot see and cannot forgive if it is wrong:
evidence that never arrives, or arrives twice, or arrives out of order after a
restart. So the collector here is a recorded callable and the laws asserted are
the durable ones —

* the cursor moves only on a 2xx (a failed batch is retried, never skipped);
* a batch that was sent is never sent again after a "restart";
* the signature is recomputable by the collector from the shared secret alone;
* retries back off, and a permanently dead collector goes `blocked` with an
  audit row instead of retrying silently forever;
* a disabled sink is silent.
"""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.audit import write_audit
from harness_identity.crypto import KeyVault
from harness_identity.models import Base, Team, TeamMember, User
from harness_identity.siem_sink import (
    MAX_ATTEMPTS,
    RETRY_BASE_SECONDS,
    RETRY_MAX_SECONDS,
    SIGNATURE_HEADER,
    STATE_BLOCKED,
    STATE_SENDING,
    SiemSink,
    push_due,
)

KEY = b"test-master-key-0123456789abcdef"
URL = "https://siem.example.com/ingest/harness"
SECRET = "collector-shared-secret-0123456789"
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
async def world(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/push.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(User(id="u1", email="a@x.z", name="a"))
        s.add(Team(id="t1", name="A"))
        s.add(TeamMember(id="m1", team_id="t1", user_id="u1", display_name="a", role="admin"))
        await s.commit()
    yield {"factory": factory, "vault": KeyVault(KEY)}
    await engine.dispose()


async def seed_rows(world, n, *, team="t1", actor="m1"):
    async with world["factory"]() as s:
        for i in range(n):
            await write_audit(
                s,
                "member.removed",
                actor_member_id=actor,
                target_type="agent_run",
                target_id=f"r{i}",
                detail={"teamId": team, "n": i},
            )


async def add_sink(world, **over):
    row = SiemSink(
        id=over.pop("id", "sink-1"),
        team_id="t1",
        label="prod",
        collector_url=URL,
        secret_encrypted=world["vault"].encrypt(SECRET),
        secret_hint="…" + SECRET[-4:],
        next_attempt_at=over.pop("next_attempt_at", NOW - timedelta(seconds=1)),
        created_by="u1",
        created_at=NOW,
        updated_at=NOW,
        **over,
    )
    async with world["factory"]() as s:
        s.add(row)
        await s.commit()
    return row


async def get_sink(world, sink_id="sink-1"):
    async with world["factory"]() as s:
        return (await s.execute(select(SiemSink).where(SiemSink.id == sink_id))).scalar_one()


class Collector:
    """A stand-in SIEM endpoint: records what it was handed, answers what it's told."""

    def __init__(self, status=204, *, raise_transport=False):
        self.status = status
        self.raise_transport = raise_transport
        self.calls = []

    async def __call__(self, url, body, headers):
        self.calls.append({"url": url, "body": body, "headers": headers})
        if self.raise_transport:
            raise OSError("connection refused")
        return self.status


def _seqs(collector):
    """The audit seqs in the single batch a collector was handed."""
    return [json.loads(line)["seq"] for line in collector.calls[0]["body"].decode().splitlines()]


def _utc(value):
    """SQLite drops the offset on read; compare instants, not adapter quirks."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------- happy path


async def test_a_due_sink_receives_its_teams_rows_as_ndjson(world):
    await add_sink(world)
    await seed_rows(world, 3)
    collector = Collector()
    totals = await push_due(world["factory"], vault=world["vault"], now=NOW, sender=collector)
    assert totals["pushed"] == 1
    lines = collector.calls[0]["body"].decode().splitlines()
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["action"] == "member.removed" and first["seq"] == 1
    assert collector.calls[0]["headers"]["Content-Type"] == "application/x-ndjson"
    sink = await get_sink(world)
    assert sink.cursor_seq == 3
    assert sink.state == "active" and sink.attempts == 0
    assert sink.delivered_rows == 3


async def test_the_signature_is_recomputable_from_the_shared_secret_alone(world):
    await add_sink(world)
    await seed_rows(world, 1)
    collector = Collector()
    await push_due(world["factory"], vault=world["vault"], now=NOW, sender=collector)
    headers = collector.calls[0]["headers"]
    ts = headers["X-Harness-Timestamp"]
    header = headers[SIGNATURE_HEADER]
    assert header.startswith(f"t={ts},v1=")
    expected = hmac.new(
        SECRET.encode(), ts.encode() + b"." + collector.calls[0]["body"], hashlib.sha256
    ).hexdigest()
    assert header.split("v1=", 1)[1] == expected
    # A collector that pins the idempotency key can drop a replayed batch.
    assert headers["Idempotency-Key"] == "sink-1:1-1"


async def test_the_wire_rows_carry_the_chain_fields_an_auditor_needs(world):
    await add_sink(world)
    await seed_rows(world, 2)
    collector = Collector()
    await push_due(world["factory"], vault=world["vault"], now=NOW, sender=collector)
    row = json.loads(collector.calls[0]["body"].decode().splitlines()[0])
    assert set(row) >= {
        "seq",
        "actorMemberId",
        "actorKind",
        "action",
        "targetType",
        "targetId",
        "detail",
        "ts",
        "chainPos",
        "prevHash",
        "rowHash",
    }


# ------------------------------------------------------- failure and the cursor


async def test_a_rejected_batch_leaves_the_cursor_where_it_was(world):
    await add_sink(world)
    await seed_rows(world, 2)
    collector = Collector(status=500)
    totals = await push_due(
        world["factory"], vault=world["vault"], now=NOW, sender=collector
    )
    assert totals["retrying"] == 1
    sink = await get_sink(world)
    assert sink.cursor_seq == 0, "a batch the collector refused must not be marked delivered"
    assert sink.state == "active" and sink.attempts == 1
    assert sink.last_status_code == 500 and sink.last_error == "http-500"


async def test_a_transport_error_is_a_retry_not_a_loss(world):
    await add_sink(world)
    await seed_rows(world, 1)
    collector = Collector(raise_transport=True)
    totals = await push_due(
        world["factory"], vault=world["vault"], now=NOW, sender=collector
    )
    assert totals["retrying"] == 1
    sink = await get_sink(world)
    assert sink.cursor_seq == 0
    assert sink.last_error == "OSError"


async def test_a_crash_between_send_and_commit_replays_instead_of_skipping(world):
    """The restart case a customer actually hits: the collector got the batch,
    the process died before the cursor moved. Re-sending the same rows under the
    same idempotency key is the safe half of at-least-once."""
    await add_sink(world)
    await seed_rows(world, 2)
    collector = Collector()
    await push_due(world["factory"], vault=world["vault"], now=NOW, sender=collector)
    assert _seqs(collector) == [1, 2]
    # Simulate losing the cursor update, then restart.
    async with world["factory"]() as s:
        row = (await s.execute(select(SiemSink).where(SiemSink.id == "sink-1"))).scalar_one()
        row.cursor_seq = 0
        row.delivered_rows = 0
        row.next_attempt_at = NOW + timedelta(minutes=1)
        await s.commit()
    again = Collector()
    await push_due(
        world["factory"], vault=world["vault"], now=NOW + timedelta(minutes=1), sender=again
    )
    assert again.calls[0]["headers"]["Idempotency-Key"] == collector.calls[0][
        "headers"
    ]["Idempotency-Key"]
    assert _seqs(again) == [1, 2]


async def test_the_backoff_doubles_and_is_capped(world):
    await add_sink(world)
    await seed_rows(world, 1)
    sink = await get_sink(world)
    assert sink.attempts == 0
    for n in range(1, 5):
        at = NOW + timedelta(seconds=RETRY_BASE_SECONDS * 2 ** (n - 1))
        await push_due(
            world["factory"], vault=world["vault"], now=at, sender=Collector(status=500)
        )
        sink = await get_sink(world)
        assert sink.attempts == n
        expected = min(RETRY_BASE_SECONDS * 2 ** (n - 1), RETRY_MAX_SECONDS)
        assert _utc(sink.next_attempt_at) == at + timedelta(seconds=expected)


async def test_a_dead_collector_is_blocked_and_says_so_in_the_trail(world):
    await add_sink(world)
    await seed_rows(world, 1)
    collector = Collector(status=503)
    totals = {}
    for n in range(MAX_ATTEMPTS):
        totals = await push_due(
            world["factory"],
            vault=world["vault"],
            now=NOW + timedelta(seconds=RETRY_BASE_SECONDS * 2**n),
            sender=Collector(status=503),
        )
    assert totals["blocked"] == 1
    sink = await get_sink(world)
    assert sink.state == STATE_BLOCKED
    sent_after_block = len(collector.calls)
    later = Collector()
    await push_due(
        world["factory"], vault=world["vault"], now=NOW + timedelta(days=1), sender=later
    )
    assert later.calls == [], "a blocked sink must stay quiet until an admin resumes it"
    assert len(collector.calls) == sent_after_block
    async with world["factory"]() as db:
        actions = [
            row[0]
            for row in (
                await db.execute(text("SELECT action FROM audit_log WHERE action LIKE 'siem.%'"))
            ).all()
        ]
    assert actions == ["siem.blocked"]


async def test_resuming_a_blocked_sink_sends_the_backlog(world):
    await add_sink(world, state=STATE_BLOCKED, attempts=MAX_ATTEMPTS)
    await seed_rows(world, 2)
    async with world["factory"]() as s:
        row = (await s.execute(select(SiemSink).where(SiemSink.id == "sink-1"))).scalar_one()
        row.state = "active"
        row.attempts = 0
        row.next_attempt_at = NOW
        await s.commit()
    collector = Collector()
    totals = await push_due(world["factory"], vault=world["vault"], now=NOW, sender=collector)
    assert totals["pushed"] == 1
    assert len(collector.calls[0]["body"].decode().splitlines()) == 2


# ------------------------------------------------------------------ restraint


async def test_a_disabled_sink_never_sends(world):
    await add_sink(world, active=False)
    await seed_rows(world, 2)
    collector = Collector()
    totals = await push_due(world["factory"], vault=world["vault"], now=NOW, sender=collector)
    assert collector.calls == []
    assert totals == {"pushed": 0, "retrying": 0, "blocked": 0, "empty": 0}


async def test_a_sink_not_yet_due_is_left_alone(world):
    await add_sink(world, next_attempt_at=NOW + timedelta(minutes=5))
    await seed_rows(world, 2)
    collector = Collector()
    await push_due(world["factory"], vault=world["vault"], now=NOW, sender=collector)
    assert collector.calls == []


async def test_a_sink_with_nothing_new_to_send_does_not_post(world):
    await add_sink(world, cursor_seq=3)
    await seed_rows(world, 3)
    collector = Collector()
    totals = await push_due(world["factory"], vault=world["vault"], now=NOW, sender=collector)
    assert collector.calls == [] and totals["empty"] == 1
    sink = await get_sink(world)
    assert sink.cursor_seq == 3 and sink.state == "active"


async def test_a_batch_is_bounded_and_the_next_batch_follows_immediately(world):
    await add_sink(world)
    await seed_rows(world, 5)
    collector = Collector()
    await push_due(
        world["factory"], vault=world["vault"], now=NOW, sender=collector, batch_rows=2
    )
    assert len(collector.calls) == 1, "one batch per tick keeps a collector's files small"
    assert len(collector.calls[0]["body"].decode().splitlines()) == 2
    sink = await get_sink(world)
    assert sink.cursor_seq == 2
    assert _utc(sink.next_attempt_at) == NOW, "rows still waiting: go again on the next tick"
    again = Collector()
    await push_due(world["factory"], vault=world["vault"], now=NOW, sender=again, batch_rows=2)
    assert again.calls[0]["headers"]["Idempotency-Key"] == "sink-1:3-4"


async def test_a_stale_claim_lease_is_reclaimed(world):
    await add_sink(world, state=STATE_SENDING, claimed_at=NOW - timedelta(hours=1))
    await seed_rows(world, 1)
    collector = Collector()
    totals = await push_due(world["factory"], vault=world["vault"], now=NOW, sender=collector)
    assert totals["pushed"] == 1
    sink = await get_sink(world)
    assert sink.state == "active" and sink.claimed_at is None


async def test_an_in_flight_claim_is_not_released_twice(world):
    await add_sink(world, state=STATE_SENDING, claimed_at=NOW - timedelta(seconds=5))
    await seed_rows(world, 1)
    collector = Collector()
    totals = await push_due(world["factory"], vault=world["vault"], now=NOW, sender=collector)
    assert collector.calls == [] and totals == {
        "pushed": 0,
        "retrying": 0,
        "blocked": 0,
        "empty": 0,
    }


async def test_one_teams_rows_are_never_handed_to_another_teams_sink(world, tmp_path):
    await add_sink(world)
    async with world["factory"]() as s:
        s.add(Team(id="t2", name="B"))
        s.add(TeamMember(id="m2", team_id="t2", user_id="u1", display_name="a", role="admin"))
        await s.commit()
    await seed_rows(world, 2, team="t2", actor="m2")
    collector = Collector()
    totals = await push_due(world["factory"], vault=world["vault"], now=NOW, sender=collector)
    assert collector.calls == [] and totals["empty"] == 1
