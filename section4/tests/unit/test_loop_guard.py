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
        assert guard.observe(_call(name="write", call_id=f"w{i}")) is None
    assert guard.calls_seen == 4
    assert guard.repeat_of("read", '{"command":"git status"}') == 2
    assert guard.repeat_of("write", '{"command":"git status"}') == 2
    # A third read reaches the limit; the writes never contributed to it.
    assert guard.observe(_call(name="read", call_id="r2")) == "loop_detected"



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
