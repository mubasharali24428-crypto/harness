"""W2 — a looping run must be caught and stopped, not left to the wall clock.

Two live acceptance attempts (2026-10-26) made 14 and 37 tool calls against one
trivial goal and were stopped by nothing but `ENGINE_RUN_TIMEOUT_SECONDS`. The
reason is not a missing budget: `maxToolCalls` exists, and
`remaining_tool_calls` (`execution.py:436`) computes it as
`limit - COUNT(*) FROM agent_tool_calls`. But that table is written by
`record_engine_tool_calls` at `service.py:1406` — **after the engine call
returns** — so during the run the count is always zero, and the one snapshot taken
at run start is the full limit. Native `tool-fs` calls are not bash calls, so the
shell plugin's own counter never sees them either.

So the guard lives where every call actually passes: the W1 transcript forwarder,
which sees one record per call/result on both execution paths. It is pure and
DB-free, because "is this run repeating itself" is a question about the event
stream, not about a table that arrives late.
"""

import pytest

from harness_orchestrator.loop_guard import LoopGuard


def _call(name="bash", arguments='{"command":"git status"}', call_id="c1"):
    return {"phase": "call", "name": name, "arguments": arguments, "callId": call_id}


def _result(call_id="c1"):
    return {"phase": "result", "callId": call_id, "ok": True}


def test_first_observation_returns_nothing():
    guard = LoopGuard(max_calls=10, repeat_limit=3)
    assert guard.observe(_call()) is None


def test_results_do_not_consume_the_budget_or_count_as_repeats():
    """A run's calls, not its acknowledgements. Counting results would halve the
    budget for no security reason and make every repeat look twice as bad."""
    guard = LoopGuard(max_calls=1, repeat_limit=2)
    assert guard.observe(_call(call_id="a")) is None
    assert guard.observe(_result(call_id="a")) is None
    assert guard.observe(_result(call_id="a")) is None
    assert guard.observe(_call(call_id="b")) == "tool_budget_exhausted"


def test_identical_call_at_the_repeat_limit_is_reported_as_a_loop():
    guard = LoopGuard(max_calls=100, repeat_limit=3)
    assert guard.observe(_call(call_id="1")) is None
    assert guard.observe(_call(call_id="2")) is None
    assert guard.observe(_call(call_id="3")) == "loop_detected"


def test_whitespace_difference_is_the_same_loop():
    """`git  status` and `git status` are one action repeated, and the whole
    point of detection is that the model re-emits near-identical commands."""
    guard = LoopGuard(max_calls=100, repeat_limit=2)
    assert guard.observe(_call(arguments='{"command":"git status"}', call_id="1")) is None
    assert (
        guard.observe(_call(arguments='{"command":"git   status"}', call_id="2"))
        == "loop_detected"
    )


def test_different_tool_names_accumulate_separately():
    """Two tools alternating must not share one repeat counter.

    The first draft of this test gave every call the same arguments with
    `repeat_limit=2` and then expected no loop — which would have been wrong to
    pass: `read` twice on identical input IS the loop the guard exists to catch.
    Stated properly, the property is that shapes are per (tool, arguments), so a
    run that alternates read/write forever is a budget burn, not a loop.
    """
    guard = LoopGuard(max_calls=None, repeat_limit=3)
    for i in range(2):
        assert guard.observe(_call(name="read", call_id=f"r{i}")) is None
        assert guard.observe(_call(name="grep", call_id=f"g{i}")) is None
    assert guard.calls_seen == 4
    assert guard.repeat_of("read", '{"command":"git status"}') == 2
    assert guard.repeat_of("grep", '{"command":"git status"}') == 2
    # A third read reaches the limit; the greps never contributed to it.
    assert guard.observe(_call(name="read", call_id="r2")) == "loop_detected"


def test_inspecting_again_after_an_edit_is_not_a_loop():
    """W20, measured: a design run took the same screenshot after each of four
    edits and was stopped as a loop. Re-checking changed state is work."""
    guard = LoopGuard(max_calls=None, repeat_limit=4)
    shot = '{"action":"screenshot"}'
    for i in range(6):
        assert guard.observe(_call(name="browser", arguments=shot, call_id=f"s{i}")) is None
        assert guard.observe(_call(name="edit", arguments=f'{{"n":{i}}}', call_id=f"e{i}")) is None


def test_identical_edits_are_still_a_loop_across_inspections():
    """The reset is for re-inspection only; re-applying one edit is going round."""
    guard = LoopGuard(max_calls=None, repeat_limit=3)
    for i in range(2):
        assert guard.observe(_call(name="edit", arguments="same", call_id=f"e{i}")) is None
        assert guard.observe(_call(name="browser", arguments="shot", call_id=f"s{i}")) is None
    assert guard.observe(_call(name="edit", arguments="same", call_id="e2")) == "loop_detected"


def test_inspection_without_an_edit_still_loops():
    guard = LoopGuard(max_calls=None, repeat_limit=3)
    for i in range(2):
        assert guard.observe(_call(name="browser", arguments="shot", call_id=f"s{i}")) is None
    assert guard.observe(_call(name="browser", arguments="shot", call_id="s2")) == "loop_detected"



def test_budget_is_counted_across_distinct_calls_not_per_shape():
    """100 different calls is a budget burn even though nothing repeats."""
    guard = LoopGuard(max_calls=3, repeat_limit=50)
    assert guard.observe(_call(call_id="1", arguments="a")) is None
    assert guard.observe(_call(call_id="2", arguments="b")) is None
    assert guard.observe(_call(call_id="3", arguments="c")) is None
    assert guard.observe(_call(call_id="4", arguments="d")) == "tool_budget_exhausted"


def test_once_stopped_it_stays_stopped():
    """The forwarder cannot un-observe a record. After the first refusal every
    later call must also be refused, or the guard reports a stop and then lets the
    run continue anyway — the exact shape of the bug this replaces."""
    guard = LoopGuard(max_calls=2, repeat_limit=50)
    guard.observe(_call(call_id="1", arguments="a"))
    assert guard.observe(_call(call_id="2", arguments="b")) is None
    assert guard.observe(_call(call_id="3", arguments="c")) == "tool_budget_exhausted"
    assert guard.observe(_call(call_id="4", arguments="d")) == "tool_budget_exhausted"


def test_no_limit_means_no_budget_stop():
    """`maxToolCalls` is optional. None must not become zero, or unset limits
    silently kill every run."""
    guard = LoopGuard(max_calls=None, repeat_limit=None)
    for i in range(200):
        assert guard.observe(_call(call_id=str(i), arguments=f"cmd {i}")) is None


def test_counts_reflect_only_calls():
    guard = LoopGuard(max_calls=10, repeat_limit=3)
    guard.observe(_call(call_id="1", arguments="a"))
    guard.observe(_result(call_id="1"))
    guard.observe(_call(call_id="2", arguments="b"))
    assert guard.calls_seen == 2
    assert guard.repeat_of("bash", "a") == 1


def test_unparsable_arguments_do_not_raise():
    """Arguments arrive as whatever the runtime serialised. A guard that throws
    on a weird payload would take the transcript down with it."""
    guard = LoopGuard(max_calls=10, repeat_limit=3)
    assert guard.observe({"phase": "call", "name": "bash", "arguments": None}) is None
    assert guard.observe({"phase": "call", "name": None, "arguments": "{"}) is None


@pytest.mark.parametrize("bad", [0, -1])
def test_nonsensical_limits_are_refused_at_construction(bad):
    """max_calls=0 stops every run before its first call; that is a
    configuration error, not a policy."""
    with pytest.raises(ValueError):
        LoopGuard(max_calls=bad, repeat_limit=3)


# ---------------------------------------------------------------- W27 repeated denials

def _denied(call_id="c1", scope=None):
    ask = f' [scope-request] {{"path":"{scope}","grants":false}}' if scope else ""
    return {"phase": "result", "callId": call_id, "ok": False, "error": "tool error",
            "output": f"Denied by the harness command policy: x. Instead: y.{ask}"}


def test_three_denials_in_a_row_stop_the_run():
    from harness_orchestrator.loop_guard import DENIALS_REPEATED
    g = LoopGuard(max_calls=None, repeat_limit=None)
    assert [g.observe(_denied(f"c{i}")) for i in range(3)] == [None, None, DENIALS_REPEATED]
    assert g.observe(_call()) == DENIALS_REPEATED  # stopped stays stopped


def test_an_allowed_result_resets_the_run_and_a_timeout_is_not_a_denial():
    timeout = {"phase": "result", "ok": True, "output": "[timed out: not a policy denial]"}
    g = LoopGuard(max_calls=None, repeat_limit=None)
    seq = [_denied(), _denied(), _result(), _denied(), _denied(), timeout, _denied(), _denied()]
    assert [g.observe(r) for r in seq] == [None] * len(seq)


def test_ten_spread_out_denials_in_fifty_results_stop_the_run():
    from harness_orchestrator.loop_guard import DENIALS_REPEATED
    g = LoopGuard(max_calls=None, repeat_limit=None)
    out = []
    for _ in range(10):
        out += [g.observe(_denied()), g.observe(_result()), g.observe(_result())]
    assert out[-3] == DENIALS_REPEATED and not any(out[:-3])


def test_scope_request_is_read_from_the_denial_and_asks_for_nothing_granted():
    from harness_orchestrator.loop_guard import scope_request_of
    assert scope_request_of(_denied(scope="docs")) == {"path": "docs", "grants": False}
    assert scope_request_of(_denied()) is None
    assert scope_request_of({"output": "[scope-request] {not json}"}) is None


# ------------------------------------------------- W28 operator-tunable denial stops

def _denial_guard(**kwargs):
    return LoopGuard(max_calls=None, repeat_limit=None, **kwargs)


def test_constructor_defaults_are_the_shipped_thresholds():
    """An unchanged caller still gets 3 in a row / 10 in 50 — the existing tests
    above construct the guard with no new kwargs and must keep passing."""
    from harness_orchestrator.loop_guard import (
        DENIAL_RUN_LIMIT,
        DENIAL_WINDOW_LIMIT,
        DENIALS_REPEATED,
    )
    assert (DENIAL_RUN_LIMIT, DENIAL_WINDOW_LIMIT) == (3, 10)
    g = LoopGuard(max_calls=None, repeat_limit=None)
    assert [g.observe(_denied(f"c{i}")) for i in range(3)] == [None, None, DENIALS_REPEATED]


def test_window_evicts_at_exactly_fifty_results():
    """The oldest denial must leave the window once 50 later results arrive."""
    from harness_orchestrator.loop_guard import DENIAL_WINDOW, DENIALS_REPEATED
    g = _denial_guard(denial_run_limit=None, denial_window_limit=2)
    assert g.observe(_denied()) is None
    for i in range(DENIAL_WINDOW):
        assert g.observe(_result(call_id=f"ok{i}")) is None
    # With no eviction, this denial would be the 2nd in the window and stop.
    assert g.observe(_denied()) is None
    assert g.observe(_denied()) == DENIALS_REPEATED


def test_interleaved_calls_and_results_do_not_miscount_denials():
    """Calls are acknowledged, not counted as results (W2's rule). If they were
    pushed into the denial window, 60 calls would evict the first denial."""
    from harness_orchestrator.loop_guard import DENIAL_WINDOW, DENIALS_REPEATED
    g = _denial_guard(denial_run_limit=None, denial_window_limit=2)
    assert g.observe(_denied()) is None
    for i in range(DENIAL_WINDOW + 10):
        assert g.observe(_call(arguments=f"cmd {i}", call_id=f"x{i}")) is None
    assert g.observe(_denied()) == DENIALS_REPEATED


def test_both_limits_disabled_means_never_stops_on_denials():
    g = _denial_guard(denial_run_limit=None, denial_window_limit=None)
    for i in range(200):
        assert g.observe(_denied(f"d{i}")) is None
    assert g.stop_reason is None
    assert g.observe(_call()) is None


def test_a_limit_of_one_stops_on_the_first_denial():
    from harness_orchestrator.loop_guard import DENIALS_REPEATED
    by_run = _denial_guard(denial_run_limit=1, denial_window_limit=None)
    assert by_run.observe(_denied()) == DENIALS_REPEATED
    assert by_run.observe(_denied()) == DENIALS_REPEATED  # stopped stays stopped
    by_window = _denial_guard(denial_run_limit=None, denial_window_limit=1)
    assert by_window.observe(_denied()) == DENIALS_REPEATED


def test_unset_knobs_return_the_shipped_defaults(monkeypatch):
    from harness_orchestrator.loop_guard import (
        DENIAL_RUN_LIMIT,
        DENIAL_WINDOW_LIMIT,
        denial_run_limit,
        denial_window_limit,
    )
    monkeypatch.delenv("HARNESS_DENIAL_RUN_LIMIT", raising=False)
    monkeypatch.delenv("HARNESS_DENIAL_WINDOW_LIMIT", raising=False)
    assert denial_run_limit() == DENIAL_RUN_LIMIT
    assert denial_window_limit() == DENIAL_WINDOW_LIMIT


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", None), ("  ", None), ("off", None), ("OFF", None),
        ("none", None), ("0", None), ("-2", None),
        ("garbage", 3), ("12.5", 3),
        ("1", 1), (" 7 ", 7),
    ],
)
def test_denial_run_knob_semantics_match_the_repeat_limit(monkeypatch, raw, expected):
    """3 is DENIAL_RUN_LIMIT: unset/garbage → default, empty/off/0 → disabled."""
    from harness_orchestrator.loop_guard import denial_run_limit
    monkeypatch.setenv("HARNESS_DENIAL_RUN_LIMIT", raw)
    assert denial_run_limit() == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("", None), ("off", None), ("0", None), ("garbage", 10), ("1", 1), ("25", 25)],
)
def test_denial_window_knob_semantics_match_the_repeat_limit(monkeypatch, raw, expected):
    """10 is DENIAL_WINDOW_LIMIT; the 50-result window itself is not a knob."""
    from harness_orchestrator.loop_guard import denial_window_limit
    monkeypatch.setenv("HARNESS_DENIAL_WINDOW_LIMIT", raw)
    assert denial_window_limit() == expected
