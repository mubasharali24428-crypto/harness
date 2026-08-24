"""M12-W2 lane E4a (W2-C2 producer side): prId persistence + pr_opened emission.

Covers the PR-open path end to end against the shared fixtures in conftest.py:
open a draft PR on an agent/<runId> branch → pull_requests row exists,
agent_runs.pr_id links it, the pr_opened envelope carries exactly
{runId, prId, url}, and the REST fallback returns the same triple.
Restart persistence = the writes are committed rows, read back on a FRESH
app instance over the same DB.
"""

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from harness_github.app import make_app
from harness_github.pr_open import run_id_from_branch

RUN_ID = "run-e4a-1"
BRANCH = f"agent/{RUN_ID}"
AUTH = {"Authorization": "Bearer tok1"}  # seeded by tests.test_service.seed


_AGENT_EVENTS_DDL = (
    # Frozen-shape agent_events (0001), sqlite-compatible: the S1 EventBus
    # persists pr_opened here during the producer flow.
    "CREATE TABLE IF NOT EXISTS agent_events ("
    " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " project_id VARCHAR(36) NOT NULL,"
    " run_id VARCHAR(36),"
    " type VARCHAR(60) NOT NULL,"
    " payload JSON NOT NULL,"
    " ts TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP)"
)


async def seed_run(db):
    """A project + its agent run working on agent/<runId> (frozen schema)."""
    async with db() as s:
        await s.execute(text(_AGENT_EVENTS_DDL))
        await s.execute(
            text(
                "INSERT INTO agent_runs (id, project_id, branch, state)"
                " VALUES (:r, 'p1', :b, 'executing')"
            ),
            {"r": RUN_ID, "b": BRANCH},
        )
        await s.commit()


def _wire_bus(app):
    """Swap the app's bus for a recorder that still persists via S1 EventBus."""
    from harness_identity.eventbus import EventBus

    recorded: list = []
    real = EventBus(app.state.session_factory)

    class Recorder:
        async def publish(self, event_type, project_id, payload, run_id=None):
            recorded.append((event_type, project_id, payload, run_id))
            return await real.publish(event_type, project_id, payload, run_id)

    app.state.bus = Recorder()
    return recorded


async def open_draft(client, branch: str = BRANCH) -> dict:
    res = await client.post(
        "/api/v1/projects/p1/pulls/draft",
        headers=AUTH,
        json={"branch": branch, "baseBranch": "main", "title": "E4a", "bodyMd": "body"},
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _last_pr_opened_row(db):
    async with db() as s:
        row = (
            await s.execute(
                text(
                    "SELECT type, payload, run_id FROM agent_events"
                    " WHERE type = 'pr_opened' ORDER BY seq DESC LIMIT 1"
                )
            )
        ).mappings().first()
    if row is not None and isinstance(row["payload"], str):  # sqlite JSON-as-str
        row = dict(row)
        row["payload"] = json.loads(row["payload"])
    return row


async def _run_row(db):
    async with db() as s:
        row = (
            await s.execute(
                text("SELECT pr_id FROM agent_runs WHERE id = :r"), {"r": RUN_ID}
            )
        ).mappings().first()
    return dict(row) if row else None


# --------------------------------------------------------------- unit: helper


def test_run_id_from_branch_convention():
    assert run_id_from_branch("agent/run-9") == "run-9"
    assert run_id_from_branch("agent/run-9 ") == "run-9"  # trailing space tolerated
    assert run_id_from_branch("feature/manual") is None  # human branch: unbound
    assert run_id_from_branch("agent/") is None  # prefix only: no id
    assert run_id_from_branch("") is None
    assert run_id_from_branch(None) is None


# ---------------------------------------------------------------- API: happy path


@pytest.mark.usefixtures("db")
async def test_pr_open_persists_prid_and_emits_payload(db, client):
    from tests.test_service import connect_user, seed

    await seed(db)
    await connect_user(db)
    await seed_run(db)
    recorded = _wire_bus(client.app)

    body = await open_draft(client)
    assert body["draft"] is True
    assert body["prId"], "response must carry the persisted prId"

    # 1. pull_requests canonical row exists and matches the wire payload.
    async with db() as s:
        pr_row = (
            await s.execute(
                text(
                    "SELECT id, project_id, run_id, github_pr_number, pr_url, status"
                    " FROM pull_requests WHERE id = :i"
                ),
                {"i": body["prId"]},
            )
        ).mappings().first()
    assert pr_row is not None
    assert pr_row["project_id"] == "p1"
    assert pr_row["run_id"] == RUN_ID
    assert pr_row["github_pr_number"] == body["number"]
    assert pr_row["pr_url"] == body["url"]

    # 2. agent_runs.pr_id links the PR to the run (migration 0017 column).
    assert await _run_row(db) == {"pr_id": body["prId"]}

    # 3. pr_opened emitted with EXACTLY {runId, prId, url} (W2-C2 contract).
    assert len(recorded) == 1
    etype, project_id, payload, ev_run_id = recorded[0]
    assert etype == "pr_opened"
    assert project_id == "p1"
    assert set(payload) == {"runId", "prId", "url"}
    assert payload == {"runId": RUN_ID, "prId": body["prId"], "url": body["url"]}
    assert ev_run_id == RUN_ID  # envelope runId set → FK-scoped delivery

    # 4. The event PERSISTED to agent_events (restart durability), same payload.
    row = await _last_pr_opened_row(db)
    assert row is not None
    assert row["type"] == "pr_opened"
    assert row["payload"] == payload
    assert row["run_id"] == RUN_ID


# ------------------------------------------------------------ restart persistence


@pytest.mark.usefixtures("db")
async def test_prid_survives_restart_fresh_app_read(db, client):
    """Simulated process restart: brand-new app instance over the SAME DB."""
    from tests.test_service import connect_user, seed

    await seed(db)
    await connect_user(db)
    await seed_run(db)
    recorded = _wire_bus(client.app)

    opened = await open_draft(client)
    prid = opened["prId"]
    assert recorded, "producer must have published"

    # Fresh app + fresh sessions against the same (not disposed) DB engine.
    fresh_app = make_app(db, client.app.state.settings)
    fresh_app.state.bus = client.app.state.bus
    async with AsyncClient(transport=ASGITransport(app=fresh_app), base_url="http://t") as fresh:
        res = await fresh.get(f"/api/v1/projects/p1/runs/{RUN_ID}/pr", headers=AUTH)
    assert res.status_code == 200, res.text
    assert res.json() == {"runId": RUN_ID, "prId": prid, "url": opened["url"]}

    # And the raw committed rows agree (no in-memory state involved).
    assert await _run_row(db) == {"pr_id": prid}
    row = await _last_pr_opened_row(db)
    assert row["payload"]["prId"] == prid


# ------------------------------------------------------------------ unbound opens


@pytest.mark.usefixtures("db")
async def test_unbounded_branch_opens_pr_without_link_or_event(db, client):
    from tests.test_service import connect_user, seed

    await seed(db)
    await connect_user(db)
    recorded = _wire_bus(client.app)

    body = await open_draft(client, branch="humans-only")
    assert "prId" not in body  # legacy shape preserved when nothing was bound
    assert recorded == []  # no run → no event, by design

    # No pull_requests row either: without a run there is no producer step at all.
    async with db() as s:
        n = (await s.execute(text("SELECT COUNT(*) FROM pull_requests"))).scalar_one()
    assert n == 0


@pytest.mark.usefixtures("db")
async def test_unknown_run_id_degrades_honestly(db, client):
    """agent/<unknown> branch: PR opens; no link possible (FK honesty)."""
    from tests.test_service import connect_user, seed

    await seed(db)
    await connect_user(db)
    recorded = _wire_bus(client.app)

    body = await open_draft(client, branch="agent/ghost-run")
    assert "prId" not in body  # the GitHub open itself must never fail
    assert recorded == []


# ------------------------------------------------------------- GET fallback edge


@pytest.mark.usefixtures("db")
async def test_get_run_pr_requires_auth(db, client):
    from tests.test_service import seed

    await seed(db)
    await seed_run(db)

    res = await client.get(f"/api/v1/projects/p1/runs/{RUN_ID}/pr")
    assert res.status_code in (401, 403), "consumer fallback must stay authenticated"

    ok = await client.get(f"/api/v1/projects/p1/runs/{RUN_ID}/pr", headers=AUTH)
    assert ok.status_code == 200
    assert ok.json() == {"runId": RUN_ID, "prId": None, "url": None}
