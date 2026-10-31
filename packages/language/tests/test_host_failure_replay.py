"""M7: a recording that failed must replay as the failure it was.

Before this milestone, replay was a successes-only mechanism: `RecordingBridge`
kept answers and dropped errors, so replaying a run that ended badly answered
`HXH403` — "no recorded answer" — and the original failure was hidden behind the
replay's own. A trace that is silent about failures is worse than no trace,
because it looks complete.

Four properties, each with the control that proves it:

* a recorded failure re-raises as the same code, status, line and message;
* a recording whose call never settled is **refused** (`HXH104`), not replayed
  around — and the format is versioned, so a v1 file cannot be re-read under v2
  semantics;
* nothing in a replay reaches the live host;
* the serialised recording carries no secret, because it leaves the process that
  wrote it.
"""

from __future__ import annotations

import dataclasses
import json

import pytest
from harness_contracts.workflow import WorkflowRequest

from harness_hx_host import (
    FakeHostBridge,
    RecordingBridge,
    ReplayBridge,
    RunResult,
    recording_from_json,
    recording_to_json,
    run_source,
)
from harness_hx_host.bridge import RECORDING_KIND, BridgeExchange, HostBridge
from harness_hx_host.contract_runner import ContractRunner
from harness_hx_host.errors import RecordingMismatch
from support import FIXTURES

FIX_ISSUE = (FIXTURES / "valid" / "fix_issue.hx").read_text(encoding="utf-8")
CEILING = (FIXTURES / "valid" / "cost_ceiling.hx").read_text(encoding="utf-8")
ALL = ("repo.read", "repo.propose_patch", "tool.test", "model.invoke")
INPUTS = {"input": {"id": "1", "title": "t", "body": "b"}}
PROPOSAL = {"summary": "s", "patch": "--- a\n+++ b\n"}
SECRET = "sk-live-abcdefghijklmnopqrstuvwx"
# The target workflow's first crossing reads README.md; this host has no such file.
FAILING_HOST_FILES = {"docs/architecture-map.md": "# map\n"}


def host(**kwargs) -> FakeHostBridge:
    kwargs.setdefault("agents", {"developer": PROPOSAL})
    return FakeHostBridge(**kwargs)


def failing_host() -> FakeHostBridge:
    """A host whose first read cannot be answered — the run dies at crossing one."""
    return host(files=dict(FAILING_HOST_FILES))


def run(bridge, source: str = FIX_ISSUE, **kwargs) -> RunResult:
    kwargs.setdefault("granted", ALL)
    kwargs.setdefault("inputs", INPUTS if source is FIX_ISSUE else {})
    return run_source(source, path="program.hx", bridge=bridge, **kwargs)


class _HostThatDies(HostBridge):
    """Answers the first read, then the host disappears mid-second call.

    This is the shape of the gap M7 has to name: the request went out, the answer
    never came, and nothing inside the run knows which happened.
    """

    def __init__(self) -> None:
        self.reads = 0

    def read_file(self, path):
        self.reads += 1
        if self.reads == 2:
            raise SystemExit(137)  # killed between the request and the answer
        return {"path": path, "text": "# demo\n"}

    def list_files(self, prefix):
        return []

    def propose_patch(self, changes):
        return {"id": "patch-1", "diff": "d"}

    def run_tests(self, target, suite):
        return {"passed": True, "detail": "exit 0"}

    def invoke_agent(self, agent, operation, payload):
        return PROPOSAL


def _recorded_failure():
    recorder = RecordingBridge(failing_host())
    outcome = run(recorder)
    return recorder, outcome


# ------------------------------------------------------- failures replay


def test_a_recorded_failing_run_replays_its_own_failure():
    recorder, first = _recorded_failure()
    assert first.status == "error", first.status
    assert first.error.code == "HXH301"

    second = run(ReplayBridge(recorder.exchanges))
    assert second.status == first.status
    assert second.error.code == first.error.code
    assert second.error.message == first.error.message
    assert (second.error.line, second.error.col) == (first.error.line, first.error.col), (
        "the replay must blame the same line the live run did"
    )


def test_the_replayed_failure_reports_the_same_contract_digest():
    """M2 pinned digest-equality for successes; M7 extends it to failures.

    Two runs whose digests agree are the same event — which is what lets Section 4
    treat a replayed failure as evidence about the original rather than a new
    verdict handed down by the replay.
    """
    live = failing_host()
    recording = RecordingBridge(live)
    request = WorkflowRequest(
        runId="run-fail",
        projectId="proj-1",
        program=FIX_ISSUE,
        profile="agent",
        grants=list(ALL),
        inputs=dict(INPUTS),
    )
    first = ContractRunner(bridge=recording).run(request)
    calls_after_live = list(live.calls)

    replayed = ContractRunner(bridge=ReplayBridge(recording.exchanges)).run(request)
    assert first.status == "error"
    assert replayed.status == first.status
    assert replayed.code == first.code
    assert replayed.digest == first.digest
    assert replayed.operations == first.operations
    assert live.calls == calls_after_live, "the replay reached the live host"


def test_a_replay_never_reaches_the_live_host_even_when_it_fails():
    live = failing_host()
    recorder = RecordingBridge(live)
    run(recorder)
    before = len(live.calls)
    run(ReplayBridge(recorder.exchanges))
    assert len(live.calls) == before, "a replay that can call the host is not a replay"


def test_replaying_a_recording_with_the_failure_removed_refuses_instead_of_inventing():
    """Negative control: the replayed failure must come from the record.

    Delete the recorded failure and the replay no longer has anything to answer
    that crossing with, so it refuses — rather than succeeding, or fabricating the
    result the host never gave.
    """
    recorder, _ = _recorded_failure()
    assert any(e.outcome == "failure" for e in recorder.exchanges)
    trimmed = [e for e in recorder.exchanges if e.outcome != "failure"]
    outcome = run(ReplayBridge(trimmed))
    assert outcome.status == "error"
    assert outcome.error.code == "HXH403"
    assert "replay has no recorded" in outcome.error.message


def test_a_recorded_failure_is_answered_only_by_the_call_it_belongs_to():
    """Arguments participate in the key, so an altered invocation is not a match."""
    recorder, _ = _recorded_failure()
    failure = next(e for e in recorder.exchanges if e.outcome == "failure")
    tampered = dataclasses.replace(
        failure,
        arguments={"path": "some-other-file.md"},
        result={"path": "x", "text": ""},
        outcome="ok",
        failure=None,
    )
    swapped = [tampered if e is failure else e for e in recorder.exchanges]
    outcome = run(ReplayBridge(swapped))
    assert outcome.error.code == "HXH403", "a record for a different call must not answer"


def test_a_spawned_failure_keeps_its_own_slot_in_the_recording():
    """Two calls in flight, one dies: the record says which, and the replay agrees."""
    source = (FIXTURES / "valid" / "concurrent_reads.hx").read_text(encoding="utf-8")
    bridge = host(files={"README.md": "# demo\n"})  # the second read has no file
    recorder = RecordingBridge(bridge)
    first = run(recorder, source=source)
    assert first.status == "error", first.error
    outcomes = [entry.outcome for entry in recorder.exchanges]
    assert outcomes.count("failure") == 1, outcomes

    replayed = run(ReplayBridge(recorder.exchanges), source=source)
    assert replayed.status == first.status
    assert replayed.error.code == first.error.code


# --------------------------------------------------- completeness, version


def test_an_unsettled_call_makes_the_recording_incomplete_and_the_replay_refuses():
    """CEILING reads two files; the host dies answering the second."""
    dying = RecordingBridge(_HostThatDies())
    with pytest.raises(SystemExit):
        run(dying, source=CEILING, granted=("repo.read",), inputs={})
    assert dying.incomplete() == ["repo.read"], "recorded, never answered"
    assert len(dying.exchanges) == 2, "the first read settled, the second did not"

    payload = recording_to_json(dying.exchanges)
    assert [e["outcome"] for e in payload["exchanges"]] == ["ok", "in-flight"]

    outcome = run(
        ReplayBridge.from_recording(payload),
        source=CEILING, granted=("repo.read",), inputs={},
    )
    assert outcome.status == "refused"
    assert outcome.error.code == "HXH104"
    assert "never settled" in outcome.error.message


def test_the_evidence_says_whether_the_trace_is_complete():
    _, outcome = _recorded_failure()
    trace = outcome.evidence()["recording"]
    assert trace["kind"] == RECORDING_KIND
    assert trace["origin"] == "recording"
    assert trace["complete"] is True, "a failure is still a settled answer"
    assert trace["settled"] == 1
    assert trace["failures"] == 1
    assert trace["unanswered"] == []


def test_a_replay_is_marked_as_a_replay_in_its_own_evidence():
    recorder, _ = _recorded_failure()
    replayed = run(ReplayBridge(recorder.exchanges))
    assert replayed.evidence()["recording"]["origin"] == "replay"
    assert run(host()).evidence()["recording"]["origin"] == "live"


def test_a_v1_recording_is_refused_rather_than_read_under_new_rules():
    """v1 recorded successes only, so its silence meant "nothing failed".

    Under v2 the same silence can mean the answer never arrived. Reading one
    format's absence as the other's fact is exactly the reinterpretation a version
    field exists to prevent, so an unlabelled or v1 file is refused outright.
    """
    for payload in ({"exchanges": []}, {"kind": "hx.recording/v1", "exchanges": []}):
        with pytest.raises(RecordingMismatch) as refused:
            recording_from_json(payload)
        assert refused.value.code == "HXH104"
        assert refused.value.status == "refused"
    with pytest.raises(RecordingMismatch, match="no exchange list"):
        recording_from_json({"kind": RECORDING_KIND})


def test_a_recording_names_codes_so_a_foreign_failure_is_refused_not_guessed():
    """Rebuild by code, never by class: an unknown code is a version problem."""
    foreign = BridgeExchange(
        operation="repo.read",
        arguments={"path": "README.md"},
        outcome="failure",
        failure={
            "code": "HXH999",
            "status": "error",
            "message": "raised by a build this one does not know",
            "line": 3,
            "col": 3,
        },
    )
    outcome = run(ReplayBridge(recording_from_json(recording_to_json([foreign]))))
    assert outcome.status == "refused"
    assert outcome.error.code == "HXH104"
    assert "HXH999" in outcome.error.message


def test_a_saved_recording_carries_no_secret_value():
    """The recording leaves the process, so it obeys the same law as the evidence."""
    recorder = RecordingBridge(host(files={"README.md": f"token {SECRET}\n"}))
    run(recorder)
    rendered = json.dumps(recording_to_json(recorder.exchanges))
    assert SECRET not in rendered
    assert "[redacted]" in rendered


def test_exchanges_the_replay_never_reached_are_counted_not_hidden():
    """"It replayed" has to mean "it replayed up to here"."""
    recorder = RecordingBridge(host())
    run(recorder, source=CEILING)  # two reads, nothing else
    assert len(recorder.exchanges) == 2

    replay = ReplayBridge(recorder.exchanges)
    stopped_short = run(replay, source=FIX_ISSUE)
    assert stopped_short.error.code == "HXH403", "no model answer was ever recorded"
    assert len(replay.replayed) + len(replay.unplayed()) == len(recorder.exchanges)
