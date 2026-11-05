"""Section-B adversarial: replay TERMINAL SEALING (crash recovery).

The live machine refuses every transition after DONE (TERMINAL_STATES), but
the event-log replay in ``OrchestratorService._rehydrate`` walks the whole
log with a flat last-write-wins map. Legacy logs written before the H-008
zombie fix can legitimately carry post-terminal rows (old versions kept
emitting RESUMED/PAUSED/PLAN_PROPOSED after a run finished). Replaying such
a log today resurrects the run into a live state — the very zombie class the
stop-tombstone guard (S1-R05c) already seals for the stop path.

These tests pin the general law: once replay reaches a terminal state, the
rest of the log is inert; only terminal seals. The final test guards the
opposite direction — over-sealing must NOT stop the scan while the run is
still live (a mid-flight review loop followed by a pause is legal history).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.state_machine import AgentState

DDL = [
    "CREATE TABLE agent_events (run_id VARCHAR(64), seq INTEGER, type VARCHAR(64), payload TEXT)",
    "CREATE TABLE agent_plans ("
    "id VARCHAR(64), run_id VARCHAR(64), goal TEXT, files_likely_to_change TEXT,"
    "steps TEXT, tests TEXT,"
    "risks TEXT, estimated_cost_usd FLOAT, forbidden_paths TEXT, expected_result TEXT,"
    "status VARCHAR(20), created_at TIMESTAMP)",
    "CREATE TABLE agent_runs (id VARCHAR(64), project_id VARCHAR(64))",
]


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/replay.db")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        for ddl in DDL:
            await conn.execute(text(ddl))
        await conn.execute(
            text("INSERT INTO agent_runs (id, project_id) VALUES ('run-x', 'proj-x')")
        )
    yield maker
    await engine.dispose()


async def _insert_log(factory, run_id: str, rows: list[tuple[str, dict]]) -> None:
    async with factory() as db:
        for seq, (etype, payload) in enumerate(rows, start=1):
            await db.execute(
                text(
                    "INSERT INTO agent_events (run_id, seq, type, payload)"
                    " VALUES (:r, :s, :t, :p)"
                ),
                {"r": run_id, "s": seq, "t": etype, "p": json.dumps(payload)},
            )
        await db.commit()


# Golden acceptance path (state values, not EventType members — the wire
# spelling is what _REHYDRATE_SQL matches).
ACCEPTED = [
    ("plan_proposed", {"planId": "p1"}),
    ("plan_approved", {"planId": "p1", "approvedAt": "2026-10-17T10:00:00+00:00"}),
    ("execution_started", {}),
    ("engine_completed", {"summary": "ok"}),
    ("done_signal_received", {}),
    ("review_passed", {"findings": {}}),
]
# Pre-H-008 zombie revision: the old machine kept emitting after acceptance.
LEGACY_ZOMBIE_TAIL = [
    ("resumed", {}),
    ("plan_proposed", {"planId": "p2"}),
    ("paused", {"reason": "user_requested"}),
]


def _service(factory) -> OrchestratorService:
    svc = OrchestratorService(session_factory=factory)
    svc.run_queue_factory = None
    return svc


async def test_replay_ignores_post_terminal_rows_in_legacy_logs(factory):
    await _insert_log(factory, "run-x", ACCEPTED + LEGACY_ZOMBIE_TAIL)
    svc = _service(factory)
    rd = await svc._ensure_run("run-x")
    sm = rd["state_machine"]

    assert sm.state is AgentState.DONE, (
        "post-terminal rows resurrected a finished run on replay "
        f"(got {sm.state})"
    )
    assert sm.plan_approved is True  # durable approval survives the tail
    # M19-N1: terminal replays stay cache-only — RAM must not hold them.
    assert "run-x" not in svc.runs


async def test_replay_is_deterministic_across_services(factory):
    await _insert_log(factory, "run-x", ACCEPTED + LEGACY_ZOMBIE_TAIL)
    states = []
    for _ in range(2):
        svc = _service(factory)
        rd = await svc._ensure_run("run-x")
        states.append(rd["state_machine"].state)
    assert states[0] == states[1] == AgentState.DONE


async def test_midflight_history_is_not_over_sealed(factory):
    """A mid-flight review loop (no done-gate) stays live: review_passed loops
    back to EXECUTING and a legal trailing pause lands PAUSED — the scan must
    NOT stop early just because REVIEW_PASSED appeared."""
    rows = [
        ("plan_proposed", {"planId": "p1"}),
        ("plan_approved", {"planId": "p1"}),
        ("execution_started", {}),
        ("paused", {"reason": "human_edit"}),  # hand-over without done-gate
        ("done_signal_received", {}),
        ("review_passed", {"findings": {}}),  # mid-flight: loops to EXECUTING
        ("paused", {"reason": "user_requested"}),  # legal trailing row
    ]
    await _insert_log(factory, "run-x", rows)
    svc = _service(factory)
    rd = await svc._ensure_run("run-x")
    assert rd["state_machine"].state is AgentState.PAUSED


async def test_stop_with_a_custom_reason_replays_as_done(factory):
    """Delegation stops a child with reason `assignment_cancelled`; replay must seal it as DONE,
    not read it as resumable PAUSED (which also made a repeat stop emit a second `paused`)."""
    rows = [
        ("plan_proposed", {"planId": "p1"}),
        ("plan_approved", {"planId": "p1"}),
        ("execution_started", {}),
        ("paused", {"runId": "run-x", "reason": "assignment_cancelled", "stopped": True}),
        ("resumed", {}),  # post-stop rows are inert
    ]
    await _insert_log(factory, "run-x", rows)
    svc = _service(factory)
    assert await svc.stop_agent("run-x", reason="assignment_cancelled") is False  # idempotent
    rd = await svc._ensure_run("run-x")
    assert rd["state_machine"].state is AgentState.DONE


async def test_paused_with_a_custom_reason_and_no_marker_stays_resumable(factory):
    rows = [
        ("plan_proposed", {"planId": "p1"}),
        ("plan_approved", {"planId": "p1"}),
        ("execution_started", {}),
        ("paused", {"reason": "human_edit"}),
    ]
    await _insert_log(factory, "run-x", rows)
    rd = await _service(factory)._ensure_run("run-x")
    assert rd["state_machine"].state is AgentState.PAUSED
