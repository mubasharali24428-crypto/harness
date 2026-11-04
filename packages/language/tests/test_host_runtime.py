"""Runtime tests: the driver's whole job, one behaviour per test.

These are the milestone's acceptance tests in executable form: a checked
workflow runs end to end; an unauthorised operation is denied (at the start and
mid-run); each budget stops the run it names; a host result that does not match
its schema fails at the boundary; cancellation takes effect on the next step;
and no secret value reaches an event.
"""

from __future__ import annotations

import json
import threading

import pytest

from harness_hx import ast as A
from harness_hx.api import check_source
from harness_hx.diagnostics import Reporter
from harness_hx.parser import parse
from harness_hx_host import (
    BudgetExhausted,
    CapabilityDenied,
    FakeHostBridge,
    RecordingBridge,
    ReplayBridge,
    Runtime,
    run_source,
)
from harness_hx_host.authority import Decision
from support import FIXTURES

FIX_ISSUE = (FIXTURES / "valid" / "fix_issue.hx").read_text(encoding="utf-8")
ALL = ("repo.read", "repo.propose_patch", "tool.test", "model.invoke")
INPUTS = {"input": {"id": "1", "title": "t", "body": "b"}}
PROPOSAL = {"summary": "s", "patch": "--- a\n+++ b\n"}


def host(**kwargs) -> FakeHostBridge:
    kwargs.setdefault("agents", {"developer": PROPOSAL})
    return FakeHostBridge(**kwargs)


def run(source: str = FIX_ISSUE, **kwargs):
    kwargs.setdefault("bridge", host())
    kwargs.setdefault("granted", ALL)
    kwargs.setdefault("inputs", INPUTS)
    return run_source(source, path="fix_issue.hx", **kwargs)


def test_the_target_workflow_runs_end_to_end_and_returns_its_bundle():
    bridge = host()
    outcome = run(bridge=bridge)
    assert outcome.status == "ok", outcome.error
    assert outcome.output.name == "ReviewBundle"
    assert outcome.output.to_json() == {
        "proposal": PROPOSAL,
        "evidence": {"passed": True, "detail": "exit 0"},
    }
    assert [call.operation for call in bridge.calls] == [
        "repo.read",
        "model.invoke",
        "model.invoke",
        "repo.propose_patch",
        "verify.tests",
    ]


def test_every_crossing_is_recorded_with_correlation_and_idempotency():
    outcome = run()
    calls = [event for event in outcome.events if event["kind"] == "call.start"]
    assert len(calls) == 5
    for event in calls:
        assert event["runId"] == outcome.run_id
        assert event["correlationId"] == outcome.correlation_id
        assert event["operationId"].startswith(outcome.run_id)
        assert len(event["idempotencyKey"]) == 32
    assert len({event["idempotencyKey"] for event in calls}) == 5


def test_a_recorded_run_replays_without_touching_the_host():
    live = host()
    recorder = RecordingBridge(live)
    first = run(bridge=recorder)
    assert first.status == "ok", first.error
    assert len(recorder.exchanges) == 5

    seen = len(live.calls)
    replay = ReplayBridge(recorder.exchanges)
    second = run_source(
        FIX_ISSUE,
        path="fix_issue.hx",
        bridge=replay,
        granted=ALL,
        inputs=INPUTS,
        run_id="replay",
        correlation_id="replay",
    )
    assert second.status == "ok", second.error
    assert second.output.to_json() == first.output.to_json()
    assert len(live.calls) == seen  # the live host saw nothing new
    assert replay.replayed == [call.operation for call in live.calls]


def test_a_replay_refuses_a_call_that_was_never_recorded():
    recorder = RecordingBridge(host())
    run(bridge=recorder)
    outcome = run_source(
        FIX_ISSUE,
        bridge=ReplayBridge(recorder.exchanges[:2]),
        granted=ALL,
        inputs=INPUTS,
    )
    assert outcome.status == "error"
    assert outcome.error.code == "HXH403"
    assert "replay has no recorded" in outcome.error.message


def test_an_ungranted_request_is_denied_before_anything_runs():
    bridge = host()
    outcome = run(bridge=bridge, granted=["repo.read"])
    assert outcome.status == "denied"
    assert outcome.events == ()
    assert bridge.calls == []
    assert outcome.error.code == "HXH401"
    assert {item["capability"] for item in outcome.error.detail["denied"]} == {
        "repo.propose_patch",
        "tool.test",
        "model.invoke",
    }


def test_the_decision_is_consulted_at_the_call_site_not_once_at_the_start():
    # A decision with no denials but a narrow grant set: only the per-call check
    # can stop this run, which is the property that makes revocation real.
    checked = check_source(FIX_ISSUE, "fix_issue.hx", "agent")
    workflow = next(decl for decl in checked.program.decls if isinstance(decl, A.WorkflowDecl))
    decision = Decision(
        requested=frozenset(ALL),
        granted=frozenset({"repo.read"}),
        denials=(),
        principal="test",
        policy="test",
    )
    runtime = Runtime(
        checked.program,
        workflow,
        decision,
        host(),
        inputs=INPUTS,
        run_id="run",
        correlation_id="run",
    )
    with pytest.raises(CapabilityDenied) as raised:
        runtime.run()
    assert raised.value.detail["operation"] == "developer.plan"
    assert raised.value.line == 41


def test_a_false_ensure_fails_the_run_with_the_programs_own_line():
    outcome = run(bridge=host(tests={"passed": False, "detail": "exit 1"}))
    assert outcome.status == "error"
    assert outcome.error.code == "HXH201"
    assert outcome.error.line == 45
    assert outcome.output is None


def test_a_host_result_that_does_not_match_its_schema_fails_at_the_boundary():
    outcome = run(bridge=host(tests={"passed": "yes", "detail": "exit 0"}))
    assert outcome.status == "error"
    assert outcome.error.code == "HXH301"
    assert "verify.tests result.passed must be Bool" in outcome.error.message


def test_a_missing_field_in_a_host_result_fails_closed():
    bridge = host()
    bridge.tests = {"passed": True}
    outcome = run(bridge=bridge)
    assert outcome.error.code == "HXH301"
    assert "missing field(s) detail" in outcome.error.message


def test_an_undeclared_field_in_a_host_result_is_not_silently_dropped():
    bridge = host()
    bridge.tests = {"passed": True, "detail": "exit 0", "raw": "log"}
    outcome = run(bridge=bridge)
    assert outcome.error.code == "HXH301"
    assert "undeclared field(s) raw" in outcome.error.message


def test_a_missing_input_is_refused_with_the_input_name():
    outcome = run(inputs={})
    assert outcome.status == "error"
    assert "input 'input' was not supplied" in outcome.error.message


def test_steps_budget_stops_the_run_at_the_named_limit():
    source = """
module small
schema Evidence {
  passed: Bool
  detail: String
}
workflow slow() -> Evidence {
  requires tool.test
  budget { steps: 1; tool_calls: 5; duration: 1m }
  let a = await verify.tests("x", suite: "unit")
  let b = await verify.tests("x", suite: "unit")
  return b
}
"""
    outcome = run_source(source, bridge=host(), granted=["tool.test"])
    assert outcome.status == "budget"
    assert outcome.error.code == "HXH501"
    assert "budget.steps (1)" in outcome.error.message


def test_tool_call_budget_stops_the_run_before_the_second_call():
    source = """
module tools
schema RepoFile {
  path: String
  text: String
}
workflow twice() -> RepoFile {
  requires repo.read
  budget { steps: 10; tool_calls: 1; duration: 1m }
  let a = await repo.read("README.md")
  let b = await repo.read("README.md")
  return b
}
"""
    outcome = run_source(source, bridge=host(), granted=["repo.read"])
    assert outcome.status == "budget"
    assert "budget.tool_calls (1)" in outcome.error.message


def test_task_budget_bounds_concurrent_spawns():
    source = """
module spawns
schema RepoFile {
  path: String
  text: String
}
workflow two() -> RepoFile {
  requires repo.read
  budget { steps: 10; tool_calls: 4; tasks: 1; duration: 1m }
  let a = spawn repo.read("README.md")
  let b = spawn repo.read("docs/architecture-map.md")
  let first = await a
  return first
}
"""
    # Two layers, deliberately: the checker counts spawns and refuses this
    # before it runs, and the runtime holds the same line when the check is
    # bypassed — a checker gap must not become an unbounded host.
    codes = {record.code for record in check_source(source, "spawns.hx", "agent").diagnostics}
    assert "HX5005" in codes

    program = parse(source, Reporter("spawns.hx"))
    workflow = next(decl for decl in program.decls if isinstance(decl, A.WorkflowDecl))
    runtime = Runtime(
        program,
        workflow,
        Decision(
            requested=frozenset({"repo.read"}),
            granted=frozenset({"repo.read"}),
            denials=(),
            principal="test",
            policy="test",
        ),
        host(),
        inputs={},
        run_id="run",
        correlation_id="run",
    )
    with pytest.raises(BudgetExhausted, match=r"budget\.tasks \(1\)"):
        runtime.run()


def test_a_spawned_call_costs_one_tool_call_not_two():
    # The spawn site spends the call; the effect must not spend it again.
    # Usage is evidence, so double counting would be a false statement.
    source = """
module once
schema RepoFile {
  path: String
  text: String
}
workflow one() -> RepoFile {
  requires repo.read
  budget { steps: 10; tool_calls: 1; tasks: 1; duration: 1m }
  let a = spawn repo.read("README.md")
  return await a
}
"""
    outcome = run_source(source, bridge=host(), granted=["repo.read"], inputs={})
    assert outcome.status == "ok", outcome.error
    assert outcome.budget["used"]["toolCalls"] == 1
    assert outcome.budget["used"]["tasks"] == 1


def test_spawned_reads_overlap_and_both_results_are_used():
    source = (FIXTURES / "valid" / "concurrent_reads.hx").read_text(encoding="utf-8")
    bridge = host(agents={"summarizer": {"text": "both read"}})
    outcome = run_source(
        source, bridge=bridge, granted=["repo.read", "model.invoke"], inputs={}
    )
    assert outcome.status == "ok", outcome.error
    assert outcome.output.to_json() == {"text": "both read"}
    assert [call.operation for call in bridge.calls] == ["repo.read", "repo.read", "model.invoke"]
    assert outcome.budget["used"]["tasks"] == 2


def test_duration_budget_is_checked_while_running():
    source = """
module slow
workflow ticks() -> Int {
  budget { steps: 10; tool_calls: 1; duration: 1s }
  let a = 1
  let b = 2
  return b
}
"""
    ticks = {"now": 0.0}

    def clock() -> float:
        ticks["now"] += 0.6
        return ticks["now"]

    outcome = run_source(source, bridge=host(), inputs={}, clock=clock)
    assert outcome.status == "budget"
    assert "budget.duration (1000ms)" in outcome.error.message


def test_cancellation_takes_effect_on_the_next_step():
    cancel = threading.Event()

    def emit(event: dict) -> None:
        if event["kind"] == "call.finished":
            cancel.set()

    outcome = run(cancel=cancel, emit=emit)
    assert outcome.status == "cancelled"
    assert outcome.error.code == "HXH601"


def test_a_secret_shaped_argument_never_reaches_an_event():
    source = """
module leak
schema Issue {
  id: String
  api_token: String
}
schema Note {
  text: String
}
workflow plan(input: Issue) -> Note {
  requires model.invoke
  budget { steps: 10; tool_calls: 2; duration: 1m }
  agent developer: Agent<Note> { role: "developer" }
  let note = await developer.plan(input)
  return note
}
"""
    outcome = run_source(
        source,
        bridge=host(agents={"developer": {"text": "ok"}}),
        granted=["model.invoke"],
        inputs={"input": {"id": "1", "api_token": "super-secret"}},
    )
    assert outcome.status == "ok", outcome.error
    rendered = json.dumps(outcome.to_json())
    assert "super-secret" not in rendered
    assert "[redacted]" in rendered


def test_the_evidence_bundle_says_who_asked_what_and_what_it_cost():
    outcome = run()
    evidence = outcome.evidence()
    assert evidence["kind"] == "hx.evidence/v1"
    assert evidence["source"]["sha256"] == outcome.source_digest
    assert evidence["source"]["bytes"] == len(FIX_ISSUE.encode("utf-8"))
    assert evidence["policy"]["granted"] == sorted(ALL)
    assert evidence["review"] == {"required": True, "approved": False, "autoMerge": False}
    assert evidence["usage"]["used"]["toolCalls"] == 5
    assert evidence["outcome"]["status"] == "ok"


def test_a_digest_that_does_not_describe_the_source_is_refused():
    outcome = run(expected_digest="0" * 64)
    assert outcome.status == "refused"
    assert outcome.error.code == "HXH103"


def test_a_program_with_check_errors_never_runs():
    outcome = run(source="workflow broken() -> Int {\n  return\n}\n", granted=ALL, inputs={})
    assert outcome.status == "refused"
    assert outcome.error.code == "HXH101"
    assert [record.code for record in outcome.diagnostics]


def test_a_program_with_several_workflows_is_refused_not_guessed():
    # Both workflows check; which one to run is not something the driver guesses.
    source = """
module two
workflow first() -> Int {
  budget { steps: 5; tool_calls: 1; duration: 1m }
  return 1
}
workflow second() -> Int {
  budget { steps: 5; tool_calls: 1; duration: 1m }
  return 2
}
"""
    outcome = run_source(source, bridge=host(), inputs={})
    assert outcome.status == "refused"
    assert outcome.error.code == "HXH102"
    assert "name one" in outcome.error.message
    named = run_source(source, bridge=host(), inputs={}, workflow="second")
    assert named.status == "ok", named.error
    assert named.output == 2


def test_an_authorised_program_still_cannot_exceed_its_own_budget_via_agents():
    source = """
module agents
schema Note {
  text: String
}
workflow plan() -> Note {
  requires model.invoke
  budget { steps: 10; tool_calls: 1; duration: 1m }
  agent developer: Agent<Note> { role: "developer" }
  let a = await developer.plan("x")
  let b = await developer.plan("y")
  return b
}
"""
    outcome = run_source(
        source, bridge=host(agents={"developer": {"text": "ok"}}), granted=["model.invoke"]
    )
    assert outcome.status == "budget"
    assert "budget.tool_calls (1)" in outcome.error.message
