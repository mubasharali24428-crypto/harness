"""M19-N2 (C8) — DONE_SIGNAL reference flow, end-to-end (pbc1 F9).

The done-signal diff body must never ride the bus:

* ``signal_done`` stores the body once (content-addressed) and stamps a
  small immutable reference onto the REVIEW_* verdict payloads the state
  machine emits — so every copy that reaches agent_events / NOTIFY /
  subscriber queues carries the pointer, not 200KB of text;
* consumers fetch table-/store-side via ``get_done_diff``, which resolves
  from the live run cache OR straight out of the durable event log (valid
  even after M19-N1 evicts terminal runs from RAM);
* plain-text evidence keeps working everywhere (legacy callers and
  recorded flows pass strings; resolve_diff treats them as the body).
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import pytest
from harness_contracts.events import EventType

# section1's EventBus is imported by path (same trick test_done_signal.py
# uses for harness_identity.audit_emit): section4's venv does not install
# harness_identity, but the delivery seam under test IS section1 code.
# The scratch-DB DDL/seed pattern mirrors test_done_signal.py's fixture law.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_S1_SRC = "/Users/user/untitled folder 2/harness/section1/src"
if _S1_SRC not in sys.path:
    sys.path.insert(0, _S1_SRC)

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from harness_orchestrator.diffstore import load_diff, save_diff  # noqa: E402
from harness_orchestrator.service import OrchestratorService  # noqa: E402
from harness_orchestrator.state_machine import AgentState  # noqa: E402

CLEAN_DIFF = "--- a/app.py\n+++ b/app.py\n@@\n print('hi')\n+print('bye')\n"

DDL = """
CREATE TABLE high_impact_approvals (id TEXT, run_id TEXT, requester_user_id TEXT, state TEXT);
CREATE TABLE teams (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    budget_usd REAL NULL
);
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
CREATE TABLE agent_plans (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL,
    goal TEXT NOT NULL,
    files_likely_to_change TEXT NOT NULL DEFAULT '[]',
    steps TEXT NOT NULL DEFAULT '[]',
    tests TEXT NOT NULL DEFAULT '[]',
    risks TEXT NOT NULL DEFAULT '[]',
    estimated_cost_usd REAL NOT NULL DEFAULT 0.05,
    forbidden_paths TEXT NOT NULL DEFAULT '[]',
    expected_result TEXT NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE cost_ledger (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id VARCHAR(36),
    agent_role VARCHAR(20) NOT NULL DEFAULT 'primary',
    model VARCHAR(120) NOT NULL DEFAULT 'stub',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd NUMERIC(12,6) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE run_claims (
    run_id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    state VARCHAR(20) NOT NULL,
    claimed_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    worker_id VARCHAR(80) NOT NULL
);
"""

SEED = [
    "INSERT INTO teams (id, name) VALUES ('team-ds', 'DS Team')",
    "INSERT INTO projects (id, team_id) VALUES ('proj-ds', 'team-ds')",
    "INSERT INTO agent_runs (id, project_id, state) VALUES ('run-ds', 'proj-ds', 'idle')",
]


@pytest.fixture
async def db_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/done_diff.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            for statement in DDL.split(";"):
                stmt = statement.strip()
                if stmt:
                    await conn.execute(text(stmt))
            for stmt in SEED:
                await conn.execute(text(stmt))

    await _setup()
    yield factory
    await engine.dispose()


def _service(factory) -> OrchestratorService:
    svc = OrchestratorService(session_factory=factory)
    svc.run_queue_factory = None
    return svc


async def _walk_to_done_gate(svc: OrchestratorService, run_id: str = "run-ds") -> None:
    plan_id = await svc.propose_plan(run_id, project_id="proj-ds")
    await svc.approve_plan(run_id, plan_id, "user-ds")
    await svc.start_execution(run_id)
    task = svc.runs[run_id].get("task")
    if task is not None:
        await task


class _InstantEngine:
    mode = "stub"

    def execute_goal(self, goal=None, session_id=None):
        return {
            "sessionId": session_id,
            "engine": "stub",
            "status": "completed",
            "finalResponse": "settled",
        }


# ---------------------------------------------------------------------------
# diffstore unit laws
# ---------------------------------------------------------------------------


def test_save_load_roundtrip_is_content_addressed(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DIFFSTORE_ROOT", str(tmp_path / "diffs"))
    ref = save_diff(CLEAN_DIFF)
    assert set(ref) == {"kind", "sha", "path", "bytes"}
    assert ref["bytes"] == len(CLEAN_DIFF.encode())
    assert load_diff(ref) == CLEAN_DIFF
    again = save_diff(CLEAN_DIFF)
    assert again == ref, "identical bodies must dedupe to one reference"


def test_load_diff_refuses_tampered_or_dangling(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DIFFSTORE_ROOT", str(tmp_path / "diffs"))
    ref = save_diff(CLEAN_DIFF)

    tampered = dict(ref, sha="0" * 64)
    assert load_diff(tampered) is None

    dangling = dict(ref, path="ff/not-there.diff")
    assert load_diff(dangling) is None

    escape = dict(ref, path="../../etc/passwd")
    assert load_diff(escape) is None


def test_resolve_diff_accepts_plain_text_legacy_body(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_DIFFSTORE_ROOT", str(tmp_path / "diffs"))
    assert resolve_plain(CLEAN_DIFF) == CLEAN_DIFF  # legacy string passthrough


def resolve_plain(evidence):
    from harness_orchestrator.diffstore import resolve_diff

    return resolve_diff(evidence)


# ---------------------------------------------------------------------------
# End-to-end: signal_done -> reference on the wire -> fetch-on-wake
# ---------------------------------------------------------------------------


async def test_done_signal_diff_never_rides_the_bus(db_factory, tmp_path, monkeypatch):
    """The full reference flow: review verdicts carry {kind, sha, path,
    bytes} only; the body resolves store-side to byte-identical text."""
    monkeypatch.setenv("HARNESS_DIFFSTORE_ROOT", str(tmp_path / "diffs"))

    svc = _service(db_factory)
    svc.engine = _InstantEngine()
    events: list[tuple] = []
    svc.on_event = lambda etype, payload: events.append((etype, payload))

    await _walk_to_done_gate(svc)
    findings = await svc.signal_done("run-ds", diff_text=CLEAN_DIFF)
    assert findings.passed is True
    sm = svc.runs["run-ds"]["state_machine"]
    assert sm.state is AgentState.DONE

    verdicts = [
        p.get("findings", {})
        for et, p in events
        if et in (EventType.REVIEW_PASSED, EventType.REVIEW_FAILED)
    ]
    assert verdicts, "a review verdict must be emitted"
    for findings_payload in verdicts:
        ref = findings_payload.get("diffRef")
        assert isinstance(ref, dict), "verdict must stamp a diffRef"
        assert ref["kind"] == "checkpoint"
        assert CLEAN_DIFF not in json_dumps(findings_payload), (
            "the BODY must not ride the wire"
        )

    # Consumer fetch-on-wake: cache path.
    assert await svc.get_done_diff("run-ds") == CLEAN_DIFF


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj)


async def test_get_done_diff_survives_ram_eviction_via_event_log(
    db_factory, tmp_path, monkeypatch
):
    """After N1-style terminal eviction, the durable REVIEW_* payload still
    carries the reference — the log itself is a valid pointer source."""
    monkeypatch.setenv("HARNESS_DIFFSTORE_ROOT", str(tmp_path / "diffs"))

    svc = _service(db_factory)
    svc.bus = EventBusOnS1Path(db_factory)  # real delivery: verdicts persist
    svc.engine = _InstantEngine()
    await _walk_to_done_gate(svc)
    await svc.signal_done("run-ds", diff_text=CLEAN_DIFF)

    # Let the fire-and-forget publish chains drain before dropping RAM.
    for _ in range(20):
        if not svc._bg_tasks:
            break
        await asyncio.gather(*svc._bg_tasks, return_exceptions=True)
        await asyncio.sleep(0)

    # N1's own law already evicted the terminal run from RAM (see service
    # INFO log); the pop below is belt-and-braces for a non-evicting store.
    svc.runs.pop("run-ds", None)  # simulate eviction / another replica
    # Fetch-on-wake WITHOUT any RAM entry: the durable REVIEW_* payload's
    # diffRef resolves through the event log.
    assert await svc.get_done_diff("run-ds") == CLEAN_DIFF


def sm_state(svc) -> AgentState:
    return svc.runs["run-ds"]["state_machine"].state


class EventBusOnS1Path:
    """The real section-1 EventBus (imported once at module scope above);
    a thin duck-type shim so the service keeps its bus seam."""

    def __init__(self, factory) -> None:
        from harness_identity.eventbus import EventBus

        self._bus = EventBus(factory)

    async def publish(self, *a, **k):
        return await self._bus.publish(*a, **k)

    async def ensure_run(self, *a, **k):
        return await self._bus.ensure_run(*a, **k)


async def test_get_done_diff_unknown_run_is_keyerror(db_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DIFFSTORE_ROOT", str(tmp_path / "diffs"))
    svc = _service(db_factory)
    with pytest.raises(KeyError):
        await svc.get_done_diff(f"never-existed-{uuid.uuid4()}")


async def test_oversized_bus_payload_is_blocked_at_publish_seam(
    db_factory, tmp_path, monkeypatch
):
    """Defense in depth: even if a future producer tried to fold the raw
    diff text into an event payload, section1's publish cap refuses it
    before any row lands. (This lane's S4 flows ship references instead.)"""
    monkeypatch.setenv("HARNESS_DIFFSTORE_ROOT", str(tmp_path / "diffs"))
    from harness_identity.eventbus import EventBus, PayloadTooLargeError

    bus = EventBus(db_factory)
    with pytest.raises(PayloadTooLargeError):
        await bus.publish(EventType.REVIEW_PASSED, "proj-ds", {"d": "x" * 10_000})
