"""Part 11: team-scoped audit/event export, and server push of collaboration events.

SQLite (the section-1 convention). The Postgres-only parts (hash chain trigger, NOTIFY)
are covered by test_audit_chain_pg.py and the migration review; here a NOTIFY is
simulated with bus.notify_collab, and the poll fallback is exercised with no wake-up at all.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from ws_driver import MiniWs

from harness_identity import domain_metrics
from harness_identity.app import make_app
from harness_identity.audit import write_audit
from harness_identity.auth import hash_token
from harness_identity.config import Settings
from harness_identity.models import AgentRun, AuthSession, Base, Project, Team, TeamMember, User

TICKETS_DDL = (
    "CREATE TABLE ws_tickets (token_hash VARCHAR(64) PRIMARY KEY, user_id VARCHAR(36) NOT NULL,"
    " team_id VARCHAR(36) NOT NULL, role VARCHAR(20) NOT NULL, project_id VARCHAR(36) NOT NULL,"
    " expires_at TIMESTAMP WITH TIME ZONE NOT NULL,"
    " created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,"
    " fingerprint VARCHAR(32))"
)
COLLAB_DDL = (
    "CREATE TABLE run_collab_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " run_id VARCHAR(36) NOT NULL, team_id VARCHAR(36) NOT NULL,"
    " actor_user_id VARCHAR(36) NOT NULL, kind VARCHAR(40) NOT NULL, request_id VARCHAR(64),"
    " payload TEXT NOT NULL DEFAULT '{}', ts TIMESTAMP, schema_version INTEGER DEFAULT 1)"
)
TOKENS = {"goodtok": ("u1", "t1"), "t2tok": ("u2", "t2"), "engtok": ("u3", "t1")}
KEY = b"test-master-key-0123456789abcdef"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("COLLAB_WS_POLL_SECONDS", "0.05")
    domain_metrics.reset()


@pytest.fixture
async def world(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/w.db", connect_args={"timeout": 15}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(COLLAB_DDL))
        await conn.execute(text(TICKETS_DDL))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        for uid in ("u1", "u2", "u3"):
            s.add(User(id=uid, email=f"{uid}@x.z", name=uid))
        s.add(Team(id="t1", name="A"))
        s.add(Team(id="t2", name="B"))
        s.add(TeamMember(id="m1", team_id="t1", user_id="u1", display_name="a", role="admin"))
        s.add(TeamMember(id="m3", team_id="t1", user_id="u3", display_name="e", role="engineer"))
        s.add(TeamMember(id="m2", team_id="t2", user_id="u2", display_name="b", role="admin"))
        s.add(Project(id="p1", team_id="t1", name="one"))
        s.add(Project(id="p2", team_id="t2", name="two"))
        await s.commit()
        s.add(AgentRun(id="r1", project_id="p1"))
        s.add(AgentRun(id="r2", project_id="p2"))
        for tok, (uid, tid) in TOKENS.items():
            s.add(AuthSession(token_hash=hash_token(tok), user_id=uid, team_id=tid,
                              expires_at=datetime.now(UTC) + timedelta(hours=1)))
        await s.commit()
    app = make_app(factory, Settings(master_key=KEY, cors_origins=[]))
    yield {"app": app, "factory": factory}
    await engine.dispose()


async def audit_row(factory, action, team, member=None, **detail):
    async with factory() as s:
        await write_audit(s, action, actor_member_id=member, target_type="agent_run",
                          target_id="r1", detail={"teamId": team, **detail})


async def collab_row(factory, run, team, kind, **payload):
    async with factory() as s:
        res = await s.execute(
            text(
                "INSERT INTO run_collab_events (run_id, team_id, actor_user_id, kind, payload, ts)"
                " VALUES (:r, :t, 'u1', :k, :p, :ts) RETURNING seq"
            ),
            {"r": run, "t": team, "k": kind, "p": json.dumps(payload), "ts": datetime.now(UTC)},
        )
        seq = res.scalar()
        await s.commit()
    return seq


async def get(app, path, token="goodtok"):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return await c.get(f"/api/v1{path}", headers=headers)


# ---------------------------------------------------------------- audit export


async def test_export_is_team_scoped_cross_team_gets_nothing_of_the_others(world):
    f, app = world["factory"], world["app"]
    await audit_row(f, "participant.joined", "t1", member="m1", secretish="alpha")
    await audit_row(f, "authz.denied", "t1", reason="NOT_LEADER")  # unbound denial, team named
    await audit_row(f, "participant.joined", "t2", member="m2")
    await audit_row(f, "authz.denied", "t2", reason="ROLE_DENIED")

    mine = (await get(app, "/audit/export")).json()
    theirs = (await get(app, "/audit/export", "t2tok")).json()
    assert {i["detail"]["teamId"] for i in mine["items"]} == {"t1"}
    assert {i["detail"]["teamId"] for i in theirs["items"]} == {"t2"}
    assert [i["action"] for i in mine["items"]][:2] == ["participant.joined", "authz.denied"]
    seqs_a = {i["seq"] for i in mine["items"]}
    seqs_b = {i["seq"] for i in theirs["items"]}
    assert not seqs_a & seqs_b  # no row is ever visible to both teams


async def test_export_has_no_team_parameter_to_widen_scope(world):
    f, app = world["factory"], world["app"]
    await audit_row(f, "authz.denied", "t2", reason="x")
    r = await get(app, "/audit/export?teamId=t2&team=t2")  # ignored by construction
    assert r.status_code == 200 and r.json()["items"] == []


async def test_export_requires_admin_and_authentication(world):
    app = world["app"]
    assert (await get(app, "/audit/export", "engtok")).status_code == 403
    assert (await get(app, "/audit/export", None)).status_code in (401, 403)
    assert (await get(app, "/audit/export?stream=collab", "engtok")).status_code == 403
    assert (await get(app, "/audit/export?stream=nope")).status_code == 422


async def test_export_is_bounded_and_pages_without_gaps_or_repeats(world):
    f, app = world["factory"], world["app"]
    for i in range(25):
        await audit_row(f, "participant.joined", "t1", member="m1", i=i)
    first = (await get(app, "/audit/export?limit=10")).json()
    assert len(first["items"]) == 10 and first["hasMore"] is True and first["limit"] == 10
    seen = [i["seq"] for i in first["items"]]
    cursor = first["nextCursor"]
    while True:
        page = (await get(app, f"/audit/export?limit=10&afterSeq={cursor}")).json()
        seen += [i["seq"] for i in page["items"]]
        cursor = page["nextCursor"]
        if not page["hasMore"]:
            break
    assert seen == sorted(set(seen)) and len(seen) >= 25  # ascending, no duplicates
    # the size bound holds however much the caller asks for
    assert (await get(app, "/audit/export?limit=999999")).json()["limit"] == 1000
    assert (await get(app, "/audit/export?limit=0")).json()["limit"] == 1


async def test_export_carries_chain_fields_and_is_itself_audited(world):
    f, app = world["factory"], world["app"]
    await audit_row(f, "participant.joined", "t1", member="m1")
    item = (await get(app, "/audit/export")).json()["items"][0]
    assert {"chainPos", "prevHash", "rowHash", "detail", "actorMemberId"} <= set(item)
    again = (await get(app, "/audit/export")).json()
    assert "audit.exported" in [i["action"] for i in again["items"]]


async def test_collab_stream_export_is_team_scoped_and_hides_cursor_markers(world):
    f, app = world["factory"], world["app"]
    mine = await collab_row(f, "r1", "t1", "redirect.guidance", content="hi")
    await collab_row(f, "r1", "t1", "guidance.delivered", upTo=mine)
    await collab_row(f, "r2", "t2", "redirect.guidance", content="secret of team two")
    out = (await get(app, "/audit/export?stream=collab")).json()
    assert [i["kind"] for i in out["items"]] == ["redirect.guidance"]
    assert "secret of team two" not in json.dumps(out)
    other = (await get(app, "/audit/export?stream=collab&runId=r2")).json()
    assert other["items"] == []  # a foreign run id yields nothing, not an error oracle


# ------------------------------------------------------------ collab push (WS)

URL = "/api/v1/ws/p1/runs/r1/collab"


async def test_connect_replays_history_in_order_then_resumes_from_a_cursor(world):
    f, app = world["factory"], world["app"]
    seqs = [await collab_row(f, "r1", "t1", "participant.joined", n=i) for i in range(5)]
    await collab_row(f, "r1", "t1", "guidance.delivered", upTo=1)  # never pushed
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq=0") as ws:
        got = await ws.recv_all()
    assert [fr["seq"] for fr in got] == seqs
    assert all(fr["channel"] == "collab" and fr["runId"] == "r1" for fr in got)
    assert got[0]["projectId"] == "p1" and got[0]["actor"] == "u1"
    # the wire contract: stable, versioned fields (part 11.001/11.002)
    assert set(got[0]) == {
        "channel", "seq", "prevSeq", "kind", "actor", "payload", "ts", "runId", "projectId",
        "teamId", "schemaVersion",
    }
    assert got[0]["teamId"] == "t1" and got[0]["schemaVersion"] == 1 and got[0]["ts"]
    assert domain_metrics.value("replayed") == 5

    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq={seqs[2]}") as ws:
        resumed = await ws.recv_all()
    assert [fr["seq"] for fr in resumed] == seqs[3:]  # nothing repeated, nothing missed


async def test_live_push_after_wakeup_and_via_poll_fallback(world):
    f, app = world["factory"], world["app"]
    bus = app.state.event_bus
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq=0") as ws:
        assert await ws.recv_all(0.2) == []
        first = await collab_row(f, "r1", "t1", "handoff.requested", to="u3")
        bus.notify_collab("r1")  # what the NOTIFY listener does
        assert (await ws.recv_json())["seq"] == first
        second = await collab_row(f, "r1", "t1", "approval.requested", approvalId="a")
        # no wake-up at all: the poll tick still delivers (HTTP-independent fallback)
        assert (await ws.recv_json())["seq"] == second
    assert domain_metrics.value("delivered") == 2


async def test_wakeups_for_already_delivered_events_are_counted_not_resent(world):
    f, app = world["factory"], world["app"]
    bus = app.state.event_bus
    seq = await collab_row(f, "r1", "t1", "participant.joined")
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq=0") as ws:
        assert [fr["seq"] for fr in await ws.recv_all(0.2)] == [seq]
        bus.notify_collab("r1")
        bus.notify_collab("r1")
        assert await ws.recv_all(0.3) == []  # nothing new, nothing repeated
    assert domain_metrics.value("duplicate") >= 1


async def test_large_gap_is_capped_with_an_honest_truncation_marker(world, monkeypatch):
    f, app = world["factory"], world["app"]
    monkeypatch.setenv("REPLAY_MAX_EVENTS", "3")
    seqs = [await collab_row(f, "r1", "t1", "participant.joined", n=i) for i in range(8)]
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq=0") as ws:
        got = await ws.recv_all()
    events = [fr for fr in got if "seq" in fr]
    assert [fr["seq"] for fr in events] == seqs[-3:]  # newest N
    marker = next(fr for fr in got if fr.get("replayTruncated"))
    assert marker["oldestSeqSent"] == seqs[-3] and marker["afterSeq"] == 0


async def test_other_teams_events_are_never_pushed(world):
    f, app = world["factory"], world["app"]
    await collab_row(f, "r1", "t2", "redirect.guidance", content="forged team id")
    mine = await collab_row(f, "r1", "t1", "participant.joined")
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq=0") as ws:
        assert [fr["seq"] for fr in await ws.recv_all()] == [mine]


@pytest.mark.parametrize(
    "url, code",
    [
        (f"{URL}", 4401),  # no credentials
        (f"{URL}?token=bogus", 4401),
        (f"{URL}?token=t2tok", 4404),  # other team's project: same code as unknown
        ("/api/v1/ws/p1/runs/r2/collab?token=goodtok", 4404),  # run not in this project
        ("/api/v1/ws/p1/runs/nope/collab?token=goodtok", 4404),
        ("/api/v1/ws/p2/runs/r2/collab?token=goodtok", 4404),  # foreign project
    ],
)
async def test_unauthorized_connections_are_closed_without_data(world, url, code):
    async with MiniWs(world["app"], url) as ws:
        assert not ws.accepted and ws.close_code == code


async def test_ticket_auth_is_shared_single_use_and_project_bound(world):
    f, app = world["factory"], world["app"]
    await collab_row(f, "r1", "t1", "participant.joined")
    ua, ip = "pytest-ua", "10.1.2.3"
    async with AsyncClient(transport=ASGITransport(app=app, client=(ip, 50000)),
                           base_url="http://t") as c:
        minted = (await c.post("/api/v1/ws-ticket", json={"projectId": "p1"},
                               headers={"Authorization": "Bearer goodtok",
                                        "User-Agent": ua})).json()
    fp = hashlib.sha256(f"{ua}{ip}".encode()).hexdigest()[:16]
    assert minted["fingerprint"] == fp
    hdrs = {"User-Agent": ua, "X-WS-Fingerprint": fp}
    async with MiniWs(app, f"{URL}?ticket={minted['ticket']}", hdrs, (ip, 123)) as ws:
        assert ws.accepted and len(await ws.recv_all()) == 1
    async with MiniWs(app, f"{URL}?ticket={minted['ticket']}", hdrs, (ip, 123)) as ws:
        assert not ws.accepted and ws.close_code == 4401  # replayed ticket


async def test_event_delivery_counters_appear_in_the_scrape_after_triggering_them(world):
    """delivered / replayed / duplicate / dropped each show up in the Prometheus text."""
    from harness_contracts import EventType

    from harness_identity.eventbus import EventBus

    f, app = world["factory"], world["app"]
    # dropped: a one-slot subscriber queue overflows on the second live publish
    bus = EventBus(f, subscriber_queue_maxsize=1)
    await bus.subscribe("p1")
    await bus.publish(EventType.TERMINAL_OUTPUT, "p1", {"n": 1})
    await bus.publish(EventType.TERMINAL_OUTPUT, "p1", {"n": 2})
    # replayed + delivered + duplicate: a collab socket catches up, then gets a live event
    await collab_row(f, "r1", "t1", "participant.joined")
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq=0") as ws:
        await ws.recv_all(0.2)
        await collab_row(f, "r1", "t1", "handoff.requested")
        app.state.event_bus.notify_collab("r1")
        await ws.recv_json()
        app.state.event_bus.notify_collab("r1")  # wake-up for an event already delivered
        await ws.recv_all(0.2)

    scrape = domain_metrics.render()
    assert "# TYPE harness_event_delivery_total counter" in scrape
    for outcome in ("replayed", "delivered", "duplicate", "dropped"):
        line = next(
            ln for ln in scrape.splitlines()
            if ln.startswith(f'harness_event_delivery_total{{outcome="{outcome}"}}')
        )
        assert int(line.rsplit(" ", 1)[1]) >= 1, scrape
