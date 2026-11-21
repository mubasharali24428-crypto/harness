"""I-08 / P-23 — the denial-learning loop (90-day plan W12).

The loop has three pins, matching the three seams it touches:

1. pure text: a run's collected scope requests become ONE deterministic,
   bounded rule suggestion (and no suggestion at all when nothing was named);
2. the wiring: a run that earns W27's ``denials_repeated`` stop carries the
   suggestion in the durable ``engine_failed`` reason — the human-visible seam
   where the correction is decided (uses the M06 service harness, real
   migrations, exactly like test_loop_guard_wiring does);
3. the metric: ``repeat_denial_stops`` counts the durable stops per project,
   which is the number the plan's acceptance line wants trending to zero.

What is deliberately NOT here: any path where the orchestrator writes the rule
itself. The suggestion ends at the suggestion; saving it is the human's act
through the rules API (see docs/extensions.md §3).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from unit.test_loop_guard_wiring import _execute

from harness_orchestrator.denial_learning import (
    MAX_SUGGESTED_PATHS,
    append_learning,
    collect_scope_request,
    repeat_denial_stops,
    suggested_rule_text,
)
from harness_orchestrator.loop_guard import DENIAL_MARKER


def _denied_result(call_id: str, path: str = "/etc/nginx/nginx.conf") -> list[dict]:
    """One denied bash call as the engine streams it: the shell plugin's refusal
    text with the scope-request tail W27 documents."""
    body = f"{DENIAL_MARKER}: path outside scope. [scope-request] {json.dumps({'path': path})}"
    return [
        {
            "phase": "call",
            "name": "bash",
            "arguments": json.dumps({"command": f"cat {path}"}),
            "callId": call_id,
        },
        {"phase": "result", "callId": call_id, "ok": False, "error": body, "output": ""},
    ]


# ---------------------------------------------------------------- 1. the text


def test_no_named_path_means_no_suggestion():
    assert suggested_rule_text("run-1", []) is None
    assert suggested_rule_text("run-1", [{"path": ""}]) is None
    assert suggested_rule_text("run-1", [{"nope": True}]) is None
    assert append_learning("denials_repeated after 3 tool calls", None) == (
        "denials_repeated after 3 tool calls"
    )


def test_suggestion_is_deterministic_deduped_and_bounded():
    paths = [{"path": f"/p{i}"} for i in range(10)]
    a = suggested_rule_text("run-9", paths)
    b = suggested_rule_text("run-9", paths)
    assert a == b
    assert "run-9" in a
    assert "/p0" in a and f"/p{MAX_SUGGESTED_PATHS - 1}" in a
    assert f"/p{MAX_SUGGESTED_PATHS}" not in a, "only the first paths are suggested"
    # duplicate path (same denial streaming twice) counts once
    dup = suggested_rule_text("run-9", [{"path": "/x"}, {"path": "/x"}])
    assert dup.count("`/x`") == 1
    assert "scopePaths" in a, "the lesson names the seam the next plan must use"


def test_append_learning_carries_the_text_into_the_durable_reason():
    detail = append_learning(
        "denials_repeated after 7 tool calls",
        suggested_rule_text("run-4", [{"path": "/etc/hosts"}]),
    )
    assert detail.startswith("denials_repeated after 7 tool calls.")
    assert "Suggested project rule" in detail
    assert "/etc/hosts" in detail


# ------------------------------------------------------- 2. the collection seam


def test_collect_scope_request_dedupes_and_bounds():
    run_data: dict = {}
    record = {"phase": "result", "ok": False, "error": _denied_result("c1")[1]["error"]}
    first = collect_scope_request(run_data, record)
    assert first == {"path": "/etc/nginx/nginx.conf", "grants": False}
    collect_scope_request(run_data, record)  # same denial again
    expected = {"path": "/etc/nginx/nginx.conf", "grants": False}
    assert run_data["_denial_scope_requests"] == [expected]
    for i in range(30):
        extra = _denied_result(f"c{i}", f"/p{i}")[1]
        collect_scope_request(run_data, {"phase": "result", "ok": False, "error": extra["error"]})
    assert len(run_data["_denial_scope_requests"]) <= 20, "a probing run cannot grow this list"


# ------------------------------------------------------------- 3. the wiring


class DenialEngine:
    """Streams three denied calls against the same outside-scope path, the way
    a model probing the sandbox boundary does."""

    api_key = None

    def __init__(self) -> None:
        self.finished = False

    def set_shell_policy(self, session_id: str, policy: dict) -> None:
        pass

    def execute_goal(self, goal=None, session_id=None, model=None, on_progress=None, images=None):
        for records in [_denied_result(f"d{i}") for i in range(3)]:
            for r in records:
                if on_progress is not None:
                    on_progress(r)
        self.finished = True
        return {
            "sessionId": session_id,
            "engine": "denial",
            "status": "completed",
            "finalResponse": "stuck on the boundary",
            "toolCalls": [],
        }


@pytest.fixture
async def _world(tmp_path):
    """The agent_events shape migration 0001 gives a real deployment — the
    columns repeat_denial_stops reads (project_id, type, payload, ts)."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/dl.db", connect_args={"timeout": 15}
    )
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE agent_events ("
                " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                " project_id VARCHAR(36) NOT NULL,"
                " run_id VARCHAR(36),"
                " type VARCHAR(60) NOT NULL,"
                " payload TEXT NOT NULL,"
                " ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def test_repeat_denial_stops_counts_durable_stops(_world):
    async with _world() as s:
        for i in range(2):
            await s.execute(
                text(
                    "INSERT INTO agent_events (project_id, run_id, type, payload)"
                    " VALUES ('p1', 'r1', 'engine_failed', :pl)"
                ),
                {"pl": json.dumps({"reason": f"denials_repeated after {i} tool calls"})},
            )
        await s.execute(
            text(
                "INSERT INTO agent_events (project_id, run_id, type, payload)"
                " VALUES ('p1', 'r1', 'engine_failed', :pl)"
            ),
            {"pl": json.dumps({"reason": "loop_detected after 4 tool calls"})},
        )
        await s.commit()
        other = await repeat_denial_stops(s, "p1")
        none = await repeat_denial_stops(s, "p-other")
    assert other["repeatDenialStops"] == 2, "loop_detected is not a denial stop"
    assert other["lastStopAt"] is not None
    assert none["repeatDenialStops"] == 0


def test_a_denial_stop_suggests_the_rule_in_the_durable_reason(tmp_path):
    engine = DenialEngine()
    _svc, run_data, seen = _execute(tmp_path, engine)

    assert run_data.get("_guard_stopped") == "denials_repeated"
    failed = [p for t, p in seen if getattr(t, "value", t) == "engine_failed"]
    assert failed, "expected a durable engine_failed event"
    reason = failed[-1]["reason"]
    assert "Suggested project rule" in reason
    assert "/etc/nginx/nginx.conf" in reason
    assert "scopePaths" in reason
    # the suggestion is also kept structured on the run record for the API
    stored = run_data.get("_suggested_rule") or ""
    assert stored and "/etc/nginx/nginx.conf" in stored
