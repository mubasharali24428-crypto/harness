"""W2 — the loop guard at the service seam.

`test_loop_guard.py` pins the decision. This file pins the plumbing that module
cannot see on its own: that the records a real engine streams actually reach the
guard, that the stop is emitted as a durable `ENGINE_FAILED`, that the shell
plugin's budget is written down to zero, and that a worker which finishes after
being told to stop cannot mark its own run complete.

It uses the M06 service harness (`test_goal_image_execution._started`) because
that fixture already proposes and approves a plan against real migrations, which
is the only way to reach `_execute` the way the stack does.

W3 adds the kill: a guarded run must call the engine's cancel, and an
unconfirmed kill must be reported as unconfirmed.

What is NOT covered here, stated so it does not read as covered: the
`tool_budget_exhausted` path at this seam. That needs a `run_limits` row with
`max_tool_calls` set, which this fixture does not create, so `budget` arrives as
`None` and only the repeat guard can fire. The budget arithmetic itself is pinned
in `test_loop_guard.py`.
"""

from unit.test_goal_image_execution import _started, run


class LoopingEngine:
    """Streams the same tool call forever, the way a stuck model does."""

    api_key = None

    def __init__(self, repeats: int = 6, tail: int = 0, killable: bool = True):
        self.repeats = repeats
        self.tail = tail
        self.policies: list[dict] = []
        self.finished = False
        self.killable = killable
        self.cancelled: list[str] = []

    def cancel(self, session_id: str) -> bool:
        """W3: the kill the spike proved. Recorded, and answerable either way."""
        self.cancelled.append(session_id)
        return self.killable

    def set_shell_policy(self, session_id: str, policy: dict) -> None:
        self.policies.append(dict(policy))

    def execute_goal(self, goal=None, session_id=None, model=None, on_progress=None, images=None):
        for i in range(self.repeats):
            if on_progress is not None:
                on_progress(
                    {
                        "phase": "call",
                        "name": "bash",
                        "arguments": '{"command":"npm test"}',
                        "callId": f"c{i}",
                    }
                )
                on_progress({"phase": "result", "callId": f"c{i}", "ok": False, "output": "boom"})
        # Some calls are left queued when the engine returns: the service drains
        # them in `finally`, and that path must consult the guard too.
        for i in range(self.tail):
            if on_progress is not None and i < self.tail - 1:
                on_progress(
                    {
                        "phase": "call",
                        "name": "read",
                        "arguments": '{"path":"package.json"}',
                        "callId": f"t{i}",
                    }
                )
        self.finished = True
        return {
            "sessionId": session_id,
            "engine": "looping",
            "status": "completed",
            "finalResponse": "I ran the tests a number of times",
            "toolCalls": [],
        }


def _execute(tmp_path, engine):
    svc, run_data, plan, _factory, _fake_read = _started(tmp_path, engine)
    seen: list[tuple] = []
    svc.on_event = lambda t, p: seen.append((t, p))
    # The state machine only reports a failure it is allowed to report:
    # `execution_failed` no-ops outside EXECUTING. `start_execution` is what puts
    # it there in the real sequence, and calling `_execute` directly skips that,
    # so without this the guard would set its flag and stay silently un-emitted —
    # a fixture bug, not a product one (found by watching this test fail).
    run_data["state_machine"].start_execution()
    run(svc._execute(run_data, plan))
    return svc, run_data, seen


def test_a_streaming_loop_fails_the_run_with_a_reason(tmp_path):
    engine = LoopingEngine(repeats=6)
    _svc, run_data, seen = _execute(tmp_path, engine)

    assert run_data.get("_guard_stopped") == "loop_detected"
    failed = [p for t, p in seen if getattr(t, "value", t) == "engine_failed"]
    assert failed, "expected a durable engine_failed event"
    assert "loop_detected" in failed[-1]["reason"]
    assert "after 4 tool calls" in failed[-1]["reason"]


def test_the_engine_never_gets_to_report_its_own_success(tmp_path):
    """A late `completed` must not overwrite the stop that already happened."""
    engine = LoopingEngine(repeats=6)
    _svc, run_data, _seen = _execute(tmp_path, engine)

    assert engine.finished is True  # it did come back
    assert run_data.get("engine_result", {}).get("status") != "completed"


def test_a_guarded_run_kills_its_worker(tmp_path):
    """W3: the stop must reach the worker, not just the event stream.

    Before this, `_guard_stopped` wrote the flag and the budget and let the
    worker burn to the wall clock. The spike showed `engine.cancel` kills a
    confined worker in under a second, so the guard now calls it.
    """
    engine = LoopingEngine(repeats=6)
    _svc, run_data, _seen = _execute(tmp_path, engine)

    # Every cancel targets this run. More than one is legitimate — the claim
    # fence and the stop path both kill, and `cancel` is idempotent by contract —
    # so the claim is "the guard asked for the kill", not "exactly one call".
    assert engine.cancelled and set(engine.cancelled) == {run_data["runId"]}
    assert run_data["_guard_killed"] is True


def test_an_unconfirmed_kill_is_not_reported_as_a_stop(tmp_path):
    """If the worker cannot be confirmed dead, the event must say so rather than
    claim a stop that did not happen."""
    engine = LoopingEngine(repeats=6, killable=False)
    _svc, run_data, seen = _execute(tmp_path, engine)

    assert run_data["_guard_killed"] is False
    failed = [p for t, p in seen if getattr(t, "value", t) == "engine_failed"]
    assert failed and "could not be confirmed" in failed[-1]["reason"]


def test_the_shell_budget_is_written_down_after_the_stop(tmp_path):
    """The plugin reads `maxToolCalls` from the policy the service hands it, so
    zeroing it is what makes a stopped run's next bash call refuse.

    The pre-stop snapshot is not asserted: this fixture has no `run_limits` row,
    so `remaining_tool_calls` legitimately reports None (no limit). The claim
    under test is the zero after the stop, not the number before it.
    """
    engine = LoopingEngine(repeats=6)
    _svc, run_data, _seen = _execute(tmp_path, engine)

    assert run_data["_guard_stopped"] == "loop_detected"
    assert [p["maxToolCalls"] for p in engine.policies] == [None, 0]


def test_a_run_under_the_repeat_limit_is_left_alone(tmp_path):
    """Two identical calls is a retry. If this test stopped the run, the guard
    would be firing on normal work."""
    engine = LoopingEngine(repeats=2)
    _svc, run_data, seen = _execute(tmp_path, engine)

    assert not run_data.get("_guard_stopped")
    assert [p for t, p in seen if getattr(t, "value", t) == "engine_failed"] == []


class ProbingEngine(LoopingEngine):
    """W27: a model that keeps asking for out-of-scope paths, each call a different command."""

    def execute_goal(self, goal=None, session_id=None, model=None, on_progress=None, images=None):
        for i in range(5):
            on_progress({"phase": "call", "name": "bash", "callId": f"p{i}",
                         "arguments": f'{{"command":"cat docs/n{i}.txt"}}'})
            on_progress({"phase": "result", "callId": f"p{i}", "ok": False, "error": "tool error",
                         "output": "Denied by the harness command policy: outside scope. Instead: "
                                   'stop. [scope-request] {"path":"docs","grants":false}'})
        self.finished = True
        return {"sessionId": session_id, "engine": "probing", "status": "completed",
                "finalResponse": "done", "toolCalls": []}


def test_repeated_denials_fail_the_run_and_carry_the_scope_request(tmp_path):
    engine = ProbingEngine(repeats=0)
    _svc, run_data, seen = _execute(tmp_path, engine)

    assert run_data.get("_guard_stopped") == "denials_repeated"
    failed = [p for t, p in seen if getattr(t, "value", t) == "engine_failed"]
    assert failed and "denials_repeated" in failed[-1]["reason"]
    results = [p for t, p in seen if getattr(t, "value", t) == "tool_result"]
    assert results and results[0]["scopeRequest"] == {"path": "docs", "grants": False}
    assert run_data.get("engine_result", {}).get("status") != "completed"
