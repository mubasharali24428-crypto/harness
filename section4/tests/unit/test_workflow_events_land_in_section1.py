"""M2 gate: a workflow's events land in Section 1, in causal order, durably.

Section 4 publishes to a bus it does not own; Section 1 owns delivery and
persistence. This test wires the REAL ``harness_identity.eventbus.EventBus``
over a scratch database (the same import-by-path seam ``test_done_diff_reference``
uses, because section4's venv does not install section 1) and then reads
``agent_events`` back — not the in-memory bus, the table.

What it proves:

* the four events of a successful run are persisted, exactly once, in causal
  order, with strictly ascending table-global ``seq``;
* every row carries the run and the project, so a transcript cannot drift into
  another run's log;
* the tool call and its result pair by ``callId`` after a round trip through
  JSONB storage;
* a second run appends its own causal block — the bus is global, the guarantee
  is per-run order, so the test drains the first run before starting the second.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from harness_contracts.events import EventType
from harness_contracts.workflow import WorkflowOperation, WorkflowResult

from harness_orchestrator.service import OrchestratorService

_S1_SRC = Path(__file__).resolve().parents[3] / "section1" / "src"
if str(_S1_SRC) not in sys.path:
    sys.path.insert(0, str(_S1_SRC))

from harness_identity.eventbus import EventBus  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

PROJECT = "proj-wf"
RUN = "run-wf"

DDL = """
CREATE TABLE teams (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) NOT NULL);
CREATE TABLE projects (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL REFERENCES teams(id)
);
CREATE TABLE agent_runs (
    id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL REFERENCES projects(id),
    state VARCHAR(40),
    brief_id VARCHAR(36),
    branch VARCHAR(200),
    pr_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE agent_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id VARCHAR(36) NOT NULL REFERENCES projects(id),
    run_id VARCHAR(36) REFERENCES agent_runs(id),
    type VARCHAR(80) NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

SEED = [
    "INSERT INTO teams (id, name) VALUES ('team-wf', 'WF Team')",
    f"INSERT INTO projects (id, team_id) VALUES ('{PROJECT}', 'team-wf')",
    f"INSERT INTO agent_runs (id, project_id, state) VALUES ('{RUN}', '{PROJECT}', 'executing')",
    f"INSERT INTO agent_runs (id, project_id, state) VALUES ('{RUN}-2', '{PROJECT}', 'executing')",
]


class OneReadRunner:
    """Honours the contract: one read, ok, a stable digest."""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, request):  # noqa: ARG002
        self.calls += 1
        return WorkflowResult(
            status="ok",
            workflow="read_one",
            output={"path": "README.md", "text": "# demo\n"},
            digest="b" * 64,
            evidence={"kind": "hx.evidence/v1"},
            operations=[
                WorkflowOperation(
                    callId=f"{request.runId}:1:repo.read",
                    operation="repo.read",
                    ok=True,
                    arguments=json.dumps({"path": "README.md"}),
                    durationMs=4,
                )
            ],
        )


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/wf-events.db")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        for statement in DDL.split(";"):
            stmt = statement.strip()
            if stmt:
                await conn.execute(text(stmt))
        for stmt in SEED:
            await conn.execute(text(stmt))
    yield maker
    await engine.dispose()


async def _drain(service: OrchestratorService) -> None:
    for _ in range(50):
        chains = list(service._publish_chains.values())
        pending = [task for task in service._bg_tasks if not task.done()]
        if not chains and not pending:
            return
        await asyncio.gather(*chains, *pending, return_exceptions=True)
        await asyncio.sleep(0)


async def _rows(factory):
    async with factory() as db:
        result = await db.execute(
            text("SELECT seq, type, payload, run_id, project_id FROM agent_events ORDER BY seq")
        )
        return result.mappings().all()


@pytest.mark.asyncio
async def test_the_transcript_is_durable_and_causally_ordered(factory):
    bus = EventBus(factory)
    service = OrchestratorService(session_factory=factory, bus=bus, workflow_runner=OneReadRunner())

    # No project_id passed: the run's project is read from agent_runs, the way
    # a request handler would find it.
    result = await service.run_workflow(RUN, "module read_one\n", workflow="read_one")
    await _drain(service)

    assert result.status == "ok"
    rows = await _rows(factory)
    assert [row.type for row in rows] == [
        EventType.EXECUTION_STARTED.value,
        EventType.TOOL_CALL.value,
        EventType.TOOL_RESULT.value,
        EventType.ENGINE_COMPLETED.value,
    ]
    seqs = [row.seq for row in rows]
    assert seqs == sorted(seqs) and len(set(seqs)) == 4
    assert {row.run_id for row in rows} == {RUN}
    assert {row.project_id for row in rows} == {PROJECT}

    payloads = [
        json.loads(row.payload) if isinstance(row.payload, str) else row.payload for row in rows
    ]
    call, settled = payloads[1], payloads[2]
    assert call["callId"] == settled["callId"] == f"{RUN}:1:repo.read"
    assert settled["ok"] is True
    assert payloads[3]["evidenceDigest"] == "b" * 64
    assert len(payloads[0]["sourceDigest"]) == 64
    assert payloads[0]["sourceDigest"] != payloads[3]["evidenceDigest"]


@pytest.mark.asyncio
async def test_two_runs_append_without_interleaving(factory):
    runner = OneReadRunner()
    bus = EventBus(factory)
    service = OrchestratorService(session_factory=factory, bus=bus, workflow_runner=runner)

    await service.run_workflow(RUN, "module read_one\n")
    await _drain(service)
    await service.run_workflow(f"{RUN}-2", "module read_one\n")
    await _drain(service)

    rows = await _rows(factory)
    by_run: dict[str, list[str]] = {}
    for row in rows:
        by_run.setdefault(row.run_id, []).append(row.type)
    assert by_run[RUN] == [
        EventType.EXECUTION_STARTED.value,
        EventType.TOOL_CALL.value,
        EventType.TOOL_RESULT.value,
        EventType.ENGINE_COMPLETED.value,
    ]
    assert by_run[f"{RUN}-2"] == by_run[RUN]
    # The second run's block starts after the first run's last row.
    last_first = max(row.seq for row in rows if row.run_id == RUN)
    first_second = min(row.seq for row in rows if row.run_id == f"{RUN}-2")
    assert first_second > last_first
    assert runner.calls == 2
