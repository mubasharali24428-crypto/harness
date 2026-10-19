"""Part 46 (lane R): the HTTP side of real-time delivery.

* ``history_page`` is the polling fallback: cursor paging with no gaps, no repeats, the socket's
  chain (``prevSeq``), team scope in SQL, ephemeral and marker kinds excluded, schema withheld.
* stale-client refusal (409 STALE_CLIENT_STATE) and the codes the system already uses.
* per-route mutation limits answer the stable 429 RATE_LIMITED envelope.
* presence heartbeats are ephemeral: they never write to the durable stream.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from harness_contracts import ratelimit as rl
from harness_contracts.ratelimit import DEFAULT_PROTECTED_ROUTES, route_matches
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator import collab, delivery
from harness_orchestrator.ratelimit_hooks import (
    ORCHESTRATOR_ROUTE_LIMITS,
    install_orchestrator_rate_limit,
)

from . import test_presence_support as support

env = support.env  # the shared presence fixture (migration 0042 on sqlite)

SRC = Path(__file__).resolve().parents[2] / "src" / "harness_orchestrator"
DDL = (
    "CREATE TABLE run_collab_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " run_id VARCHAR(36) NOT NULL, team_id VARCHAR(36) NOT NULL,"
    " actor_user_id VARCHAR(36) NOT NULL, kind VARCHAR(40) NOT NULL, request_id VARCHAR(64),"
    " payload TEXT NOT NULL DEFAULT '{}', ts TIMESTAMP, schema_version INTEGER DEFAULT 1)",
    "CREATE TABLE rate_buckets (bucket_key VARCHAR(200) PRIMARY KEY,"
    " window_start TIMESTAMP NOT NULL, count INTEGER NOT NULL DEFAULT 0)",
)


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/d.db")
    async with engine.begin() as conn:
        for ddl in DDL:
            await conn.execute(text(ddl))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def add(factory, run, team, kind, *, schema=1, **payload):
    async with factory() as db:
        res = await db.execute(
            text(
                "INSERT INTO run_collab_events (run_id, team_id, actor_user_id, kind, payload,"
                " ts, schema_version) VALUES (:r, :t, 'u1', :k, :p, :ts, :v) RETURNING seq"
            ),
            {"r": run, "t": team, "k": kind, "p": json.dumps(payload), "v": schema,
             "ts": datetime.now(UTC)},
        )
        seq = res.scalar()
        await db.commit()
    return seq


async def drain(factory, team, run, limit, after=0):
    """Follow nextCursor the way a polling client does."""
    events, pages = [], 0
    while True:
        page = await delivery.history_page(factory, team, run, after, limit)
        events += page["events"]
        pages += 1
        after = page["nextCursor"]
        if not page["hasMore"]:
            return events, page, pages


# --------------------------------------------------------- polling fallback (005)


async def test_paging_by_cursor_neither_skips_nor_repeats_and_keeps_the_chain(factory):
    truth = []
    for i in range(23):
        truth.append(await add(factory, "r1", "t1", "participant.joined", n=i))
        if i % 3 == 0:
            await add(factory, "r2", "t1", "participant.joined")  # another run in between
    for limit in (1, 4, 7, 23, 200):
        events, last, _pages = await drain(factory, "t1", "r1", limit)
        assert [e["seq"] for e in events] == truth, limit
        assert [e["prevSeq"] for e in events] == [None, *truth[:-1]], limit  # across pages too
        assert last["hasMore"] is False and last["nextCursor"] == truth[-1]


async def test_page_shape_head_and_empty_pages(factory):
    a = await add(factory, "r1", "t1", "handoff.requested", to="u2")
    page = await delivery.history_page(factory, "t1", "r1", 0, 200)
    assert page["schemaVersion"] == delivery.SCHEMA_VERSION == 1
    assert page["headSeq"] == a and page["nextCursor"] == a and page["hasMore"] is False
    (event,) = page["events"]
    assert event == {
        "seq": a, "prevSeq": None, "actor": "u1", "kind": "handoff.requested",
        "payload": {"to": "u2"}, "ts": event["ts"], "schemaVersion": 1, "runId": "r1",
    }
    assert datetime.fromisoformat(event["ts"])  # one timestamp format, same as the socket
    empty = await delivery.history_page(factory, "t1", "r1", a, 200)
    assert empty["events"] == [] and empty["nextCursor"] == a and empty["hasMore"] is False
    nothing = await delivery.history_page(factory, "t1", "ghost", 0, 200)
    assert nothing["events"] == [] and nothing["headSeq"] == 0 and nothing["nextCursor"] == 0


async def test_hasmore_is_exact_at_the_page_boundary(factory):
    for i in range(4):
        await add(factory, "r1", "t1", "participant.joined", n=i)
    exact = await delivery.history_page(factory, "t1", "r1", 0, 4)
    assert len(exact["events"]) == 4 and exact["hasMore"] is False
    short = await delivery.history_page(factory, "t1", "r1", 0, 3)
    assert len(short["events"]) == 3 and short["hasMore"] is True


async def test_page_size_is_clamped(factory):
    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO run_collab_events (run_id, team_id, actor_user_id, kind, ts)"
                " VALUES ('r1', 't1', 'u1', 'participant.joined', CURRENT_TIMESTAMP)"
            )
        )
        for _ in range(9):  # doubling insert: 2**9 + 1 rows
            await db.execute(
                text(
                    "INSERT INTO run_collab_events (run_id, team_id, actor_user_id, kind, ts)"
                    " SELECT run_id, team_id, actor_user_id, kind, ts FROM run_collab_events"
                )
            )
        await db.commit()
    assert len((await delivery.history_page(factory, "t1", "r1", 0, 10**6))["events"]) == 500
    assert len((await delivery.history_page(factory, "t1", "r1", 0, 0))["events"]) == 1
    assert (await delivery.history_page(factory, "t1", "r1", -5, 2))["events"][0]["seq"] == 1


async def test_other_teams_rows_are_invisible_and_do_not_enter_the_chain(factory):
    a = await add(factory, "r1", "t1", "participant.joined")
    await add(factory, "r1", "t2", "redirect.guidance", content="belongs to team two")
    b = await add(factory, "r1", "t1", "handoff.requested")
    page = await delivery.history_page(factory, "t1", "r1", 0, 200)
    assert [(e["seq"], e["prevSeq"]) for e in page["events"]] == [(a, None), (b, a)]
    assert "team two" not in json.dumps(page)
    theirs = await delivery.history_page(factory, "t2", "r1", 0, 200)
    assert len(theirs["events"]) == 1 and theirs["events"][0]["payload"]["content"]


# ------------------------------------------- critical vs ephemeral (007), schema (008)


async def test_markers_and_ephemeral_kinds_are_not_in_the_durable_page_or_its_chain(factory):
    a = await add(factory, "r1", "t1", "participant.joined")
    await add(factory, "r1", "t1", "guidance.delivered", upTo=a)
    for kind in ("presence.heartbeat", "typing.started", "cursor.moved"):
        await add(factory, "r1", "t1", kind)
    b = await add(factory, "r1", "t1", "approval.requested")
    page = await delivery.history_page(factory, "t1", "r1", 0, 200)
    assert [(e["seq"], e["prevSeq"]) for e in page["events"]] == [(a, None), (b, a)]
    assert page["headSeq"] == b


def test_heartbeats_write_nothing_to_the_durable_stream(env):
    """The ephemeral signal is polled (GET presence), never an event: a heartbeat storm
    cannot grow, reorder or flood run_collab_events."""
    env.seat("r1", "lead", "leader")
    for _ in range(5):
        assert env.beat("lead").status_code == 200
    env.tick(seconds=10)
    assert env.beat("lead", state="idle").status_code == 200
    assert env.kinds() == []
    assert env.presence("lead")["lead"]["presence"]["status"] in ("online", "idle")  # by polling
    page = asyncio.run(delivery.history_page(env.factory, "team-a", "r1", 0, 50))
    assert page["events"] == [] and page["headSeq"] == 0


async def test_newer_schema_rows_keep_their_seat_but_not_their_body(factory):
    a = await add(factory, "r1", "t1", "participant.joined", who="x")
    b = await add(factory, "r1", "t1", "future.kind", schema=2, newer="field")
    old = await delivery.history_page(factory, "t1", "r1", 0, 200)
    new = await delivery.history_page(factory, "t1", "r1", 0, 200, client_schema=2)
    assert [e["seq"] for e in old["events"]] == [a, b]
    assert old["events"][1]["payload"] == {"omitted": True, "reason": "schema_unsupported"}
    assert old["events"][1]["schemaVersion"] == 2 and old["events"][1]["prevSeq"] == a
    assert new["events"][1]["payload"] == {"newer": "field"}


async def test_large_bodies_are_returned_whole_this_is_where_the_socket_marker_is_refetched(
    factory,
):
    big = "x" * (delivery.PAYLOAD_MAX_BYTES * 2)
    seq = await add(factory, "r1", "t1", "redirect.guidance", content=big)
    (event,) = (await delivery.history_page(factory, "t1", "r1", seq - 1, 5))["events"]
    assert event["payload"]["content"] == big


# --------------------------------------------------------- stale clients (009)


def test_a_client_behind_the_server_is_refused_with_the_stable_409():
    with pytest.raises(delivery.StaleClientState) as stale:
        delivery.require_current_version(2, 5, "the plan", conflictFields=["goal"])
    e = stale.value
    assert (e.status, e.code) == (409, "STALE_CLIENT_STATE")
    assert e.extra == {"currentVersion": 5, "conflictFields": ["goal"]}
    assert isinstance(e, collab.CollabError)  # the existing route layers already map it


def test_current_missing_and_impossible_versions():
    delivery.require_current_version(5, 5, "x")  # up to date
    delivery.require_current_version(None, 5, "x")  # no version carried: nothing to compare
    with pytest.raises(collab.CollabError) as ahead:
        delivery.require_current_version(6, 5, "x")  # a bug on the client, not staleness
    assert (ahead.value.status, ahead.value.code) == (422, "BAD_VERSION")


def test_every_stale_code_a_client_is_told_about_really_exists_in_the_source():
    files = ("control.py", "collab.py", "delivery.py")
    emitted = "".join((SRC / name).read_text() for name in files)
    for code in delivery.STALE_CODES:
        assert f'"{code}"' in emitted, code


# --------------------------------------------------- mutation rate limits (006)

SAMPLE = {
    "/api/v1/orchestrator/runs/{runId}/redirect": "/api/v1/orchestrator/runs/r-1/redirect",
    "/api/v1/orchestrator/runs/{runId}/handoff/{handoffId}":
        "/api/v1/orchestrator/runs/r-1/handoff/h-9",
}


def test_limits_cover_real_mutation_routes_and_never_a_polled_path():
    app = FastAPI()
    app.include_router(orch_api.router)
    paths = app.openapi()["paths"]
    for template, budget in delivery.MUTATION_ROUTE_LIMITS.items():
        methods = set(paths[template])  # KeyError: the table names a route that does not exist
        assert methods <= {"post", "put"}, f"{template} also serves {methods}: a poll burns it"
        assert 30 <= budget <= 600
    # the three redirect/handoff/approval families are all covered
    listed = " ".join(delivery.MUTATION_ROUTE_LIMITS)
    for part in ("/redirect", "/handoff", "/decision", "/claim", "/complete", "/apply", "/leader"):
        assert part in listed
    assert "/runs/{runId}/approvals" not in delivery.MUTATION_ROUTE_LIMITS  # shared with the poll


def test_templates_match_concrete_paths_and_do_not_overlap_the_defaults():
    for template, path in SAMPLE.items():
        assert route_matches(template, path)
    assert not route_matches(
        "/api/v1/orchestrator/runs/{runId}/handoff", "/api/v1/orchestrator/runs/r-1/handoff/h-9"
    )
    assert not set(delivery.MUTATION_ROUTE_LIMITS) & set(DEFAULT_PROTECTED_ROUTES)


class _Clock:
    now_value = 4_000_000.0

    def time(self):
        return self.now_value


async def test_a_flood_of_redirects_gets_the_stable_RATE_LIMITED_envelope(factory, monkeypatch):
    monkeypatch.setattr(rl, "time", _Clock())  # a fixed window: the burst cannot straddle two
    template = "/api/v1/orchestrator/runs/{runId}/redirect"
    budget = delivery.MUTATION_ROUTE_LIMITS[template]
    app = FastAPI()

    @app.post(template)
    async def redirect(runId: str):  # noqa: N803 - the route's own parameter name
        return {"ok": True}

    install_orchestrator_rate_limit(
        app,
        session_factory=factory,
        routes=(*DEFAULT_PROTECTED_ROUTES, *delivery.MUTATION_ROUTE_LIMITS),
        route_limits={**ORCHESTRATOR_ROUTE_LIMITS, **delivery.MUTATION_ROUTE_LIMITS},
    )
    transport = ASGITransport(app=app, client=("10.46.0.1", 4000))
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        for i in range(budget):
            assert (await c.post(f"/api/v1/orchestrator/runs/r-{i}/redirect")).status_code == 200
        blocked = await c.post("/api/v1/orchestrator/runs/r-new/redirect")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert int(blocked.headers["retry-after"]) >= 1
