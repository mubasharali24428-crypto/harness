"""M2: the driver IS a ``WorkflowRunner`` — and a replay reproduces its digest.

Section 4 never imports ``harness_hx_host``. This file proves the other half of
that arrangement: the adapter satisfies the contract's Protocol, translates a
run honestly (status, code, operations, evidence), and produces a digest that
two runs over the same host answers agree on — which is what makes "replay"
mean something stronger than "it probably does the same thing".

The digest is the interesting part. It is computed over a canonical projection
of the evidence with wall-clock time removed: a live run and its replay must
agree, and a recorder that answered differently would be caught here rather
than in production.
"""

from __future__ import annotations

import json

import pytest
from harness_contracts.workflow import WorkflowRequest, WorkflowResult, WorkflowRunner

from harness_hx_host import ContractRunner, FakeHostBridge, RecordingBridge, ReplayBridge

READ_ONE = """\
module read_one
schema RepoFile { path: String  text: String }
workflow read_one() -> RepoFile {
  requires repo.read
  budget { steps: 20; tool_calls: 2; duration: 1m }
  let readme = await repo.read("README.md")
  ensure readme.text != ""
  return readme
}
"""

TWO_READS = """\
module two_reads
schema RepoFile { path: String  text: String }
workflow two_reads() -> RepoFile {
  requires repo.read
  budget { steps: 20; tool_calls: 1; duration: 1m }
  let first = await repo.read("README.md")
  let second = await repo.read("docs/architecture-map.md")
  return second
}
"""

UNKNOWN_TYPE = """\
module broken
workflow broken() -> Nope {
  return 1
}
"""


def request(program: str = READ_ONE, **overrides) -> WorkflowRequest:
    fields = {
        "runId": "run-1",
        "projectId": "proj-1",
        "program": program,
        "workflow": None,
        "grants": ["repo.read"],
    }
    fields.update(overrides)
    return WorkflowRequest(**fields)


def runner(bridge=None, **kwargs) -> ContractRunner:
    return ContractRunner(bridge=bridge or FakeHostBridge(), **kwargs)


def test_the_adapter_is_a_workflow_runner_by_the_contracts_own_check():
    assert isinstance(runner(), WorkflowRunner)


def test_an_ok_run_translates_into_contract_shapes():
    live = FakeHostBridge()
    result = runner(live).run(request())

    assert isinstance(result, WorkflowResult)
    assert result.status == "ok"
    assert result.workflow == "read_one"
    assert result.output == {"path": "README.md", "text": "# demo\n"}
    assert len(result.digest) == 64 and int(result.digest, 16) >= 0
    assert result.evidence["kind"] == "hx.evidence/v1"
    assert [(op.operation, op.ok) for op in result.operations] == [("repo.read", True)]
    assert json.loads(result.operations[0].arguments) == {"path": "README.md"}
    assert live.calls[0].operation == "repo.read"
    assert result.diagnostics == []


def test_an_empty_grant_list_is_a_denial_not_an_error():
    result = runner().run(request(grants=[]))

    assert result.status == "denied"
    assert result.code == "HXH401"
    assert result.operations == []
    assert result.evidence["policy"]["allowed"] is False
    assert result.evidence["policy"]["denied"][0]["capability"] == "repo.read"


def test_a_program_the_checker_refuses_never_reaches_the_bridge():
    live = FakeHostBridge()
    result = runner(live).run(request(UNKNOWN_TYPE, grants=["repo.read"]))

    assert result.status == "refused"
    assert result.code == "HXH101"
    assert result.operations == []
    assert live.calls == []


def test_a_budget_stop_keeps_the_calls_that_actually_crossed():
    live = FakeHostBridge()
    result = runner(live).run(request(TWO_READS))

    assert result.status == "budget"
    assert result.code == "HXH501"
    assert [op.operation for op in result.operations] == ["repo.read"]
    assert len(live.calls) == 1, "the second call must never reach the host"


def test_a_replay_reproduces_the_digest_without_touching_the_host():
    live = FakeHostBridge()
    recording = RecordingBridge(live)
    first = runner(recording).run(request())
    calls_after_live = list(live.calls)

    replay = ReplayBridge(recording.exchanges)
    second = runner(replay).run(request())

    assert second.status == first.status
    assert second.code == first.code
    assert second.output == first.output
    # durationMs is wall-clock, re-measured on every run (the digest excludes it by
    # design, contract_runner.py docstring); a 0 ms vs 1 ms boundary failed seed 10.
    def no_time(ops):
        return [op.model_dump(exclude={"durationMs"}) for op in ops]

    assert no_time(second.operations) == no_time(first.operations)
    assert second.digest == first.digest
    assert second.evidence["policy"] == first.evidence["policy"]
    assert live.calls == calls_after_live, "a replay must not reach the live host"
    assert replay.replayed == [call.operation for call in live.calls]


def test_a_replay_missing_an_exchange_fails_honestly():
    live = FakeHostBridge()
    recording = RecordingBridge(live)
    assert runner(recording).run(request()).status == "ok"

    # The recorded answer is consumed once; a second replay has nothing left.
    replay = ReplayBridge(recording.exchanges)
    assert runner(replay).run(request()).status == "ok"
    starved = runner(replay).run(request())

    assert starved.status == "error"
    assert starved.code == "HXH403"
    assert "never falls back" in starved.message


def test_the_digest_ignores_the_clock_but_not_the_answers():
    live = FakeHostBridge()
    ticks = iter(range(0, 10_000))
    slow = runner(live, clock=lambda: float(next(ticks)))
    fast = runner(live, clock=lambda: 0.0)

    assert slow.run(request()).digest == fast.run(request()).digest

    different = FakeHostBridge(files={"README.md": "# another demo\n"})
    assert runner(different).run(request()).digest != runner(FakeHostBridge()).run(request()).digest


@pytest.mark.parametrize(("grants", "principal"), [(["repo.read"], None), ([], ["read"])])
def test_the_request_carries_the_hosts_authority_verbatim(grants, principal):
    result = runner().run(request(grants=grants, principalPermissions=principal))

    assert result.evidence["policy"]["granted"] == sorted(grants)
    if not grants:
        assert result.status == "denied"
