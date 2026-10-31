"""M6: authority the host can walk back while the run is in flight.

``Decision`` was fixed for the life of a run, which made revocation mean "cancel
and restart" — losing the work the run was still allowed to finish. The runtime
already asked ``allows()`` at every call site; :class:`RevisableDecision` gives
that answer a way to change.

Three properties make the mechanism trustworthy rather than merely available, and
each is tested from the outside in: a revocation bites the *next* crossing and
never a completed one; the event log shows the revocation between the crossings
it separated; and authority handed in from the host is verified, not trusted.
"""

from __future__ import annotations

from harness_hx.api import check_source
from harness_hx.manifest import build_manifest
from harness_hx_host import (
    Decision,
    FakeHostBridge,
    Pricing,
    RevisableDecision,
    as_revisable,
    authorize,
    run_source,
)
from harness_hx_host.authority import authority_problems, requested_capabilities
from support import FIXTURES

FIX_ISSUE = (FIXTURES / "valid" / "fix_issue.hx").read_text(encoding="utf-8")
ALL = ("repo.read", "repo.propose_patch", "tool.test", "model.invoke")
INPUTS = {"input": {"id": "1", "title": "t", "body": "b"}}
PROPOSAL = {"summary": "s", "patch": "--- a\n+++ b\n"}
# The target workflow's crossings in order; the fifth is verify.tests (tool.test).
ORDER = ["repo.read", "model.invoke", "model.invoke", "repo.propose_patch", "verify.tests"]


class _HostThatRevokes:
    """Delegate to a fake host, withdrawing a capability after its Nth crossing.

    The revocation belongs to the host, not the program: it happens between two
    crossings, from wherever an operator's decision arrives.
    """

    def __init__(self, inner, decision, *, after: int, capability: str, reason: str) -> None:
        self.inner = inner
        self.decision = decision
        self.after = after
        self.capability = capability
        self.reason = reason
        self.calls: list[str] = []

    def _crossed(self) -> None:
        if len(self.calls) == self.after:
            self.decision.revoke(self.capability, reason=self.reason)

    def read_file(self, path):
        out = self.inner.read_file(path)
        self.calls.append("repo.read")
        return self._then(out)

    def list_files(self, prefix):
        out = self.inner.list_files(prefix)
        self.calls.append("repo.list")
        return self._then(out)

    def propose_patch(self, changes):
        out = self.inner.propose_patch(changes)
        self.calls.append("repo.propose_patch")
        return self._then(out)

    def run_tests(self, target, suite):
        out = self.inner.run_tests(target, suite)
        self.calls.append("verify.tests")
        return self._then(out)

    def invoke_agent(self, agent, operation, payload):
        out = self.inner.invoke_agent(agent, operation, payload)
        self.calls.append("model.invoke")
        return self._then(out)

    def _then(self, out):
        self._crossed()
        return out


def _manifest(source: str = FIX_ISSUE) -> dict:
    return build_manifest(check_source(source, "fix_issue.hx", "agent"), "agent")


def _decision(source: str = FIX_ISSUE, *, granted=ALL, principal="op", policy="test") -> Decision:
    return authorize(_manifest(source), granted=list(granted), principal=principal,
                     policy=policy)


def _revoking_run(*, after=4, capability="tool.test", granted=ALL, source=FIX_ISSUE, **kwargs):
    decision = as_revisable(_decision(source, granted=granted))
    bridge = _HostThatRevokes(
        FakeHostBridge(agents={"developer": PROPOSAL}),
        decision,
        after=after,
        capability=capability,
        reason="operator closed the test runner",
    )
    outcome = run_source(
        source,
        path="fix_issue.hx",
        bridge=bridge,
        inputs=INPUTS,
        authority=decision,
        **kwargs,
    )
    return outcome, bridge, decision


def test_a_revoked_capability_denies_the_next_crossing_not_the_completed_ones():
    outcome, bridge, _ = _revoking_run()
    assert outcome.status == "denied"
    assert outcome.error.code == "HXH401"
    assert bridge.calls == ORDER[:4], "the run kept the work it was still allowed to finish"
    assert outcome.error.detail["capability"] == "tool.test"
    assert outcome.error.detail["revoked"] is True
    assert "revoked mid-run" in outcome.error.message


def test_a_denial_at_the_start_says_not_granted_and_crosses_nothing():
    """The authorisation path: an ungranted request stops the run before step one."""
    outcome, bridge, _ = _revoking_run(
        after=99, granted=("repo.read", "repo.propose_patch", "model.invoke")
    )
    assert outcome.status == "denied"
    assert outcome.error.code == "HXH401"
    assert bridge.calls == [], "the run never started, so nothing was crossed"
    assert {denied["capability"] for denied in outcome.error.detail["denied"]} == {"tool.test"}


def test_a_call_site_denial_that_was_never_a_grant_says_so_instead():
    """A host may hand in authority narrower than it claimed: the run gets partway.

    ``denials`` is empty, so the start check passes and the missing grant is only
    discovered at the crossing that needed it. The message must say "did not
    grant" — calling that a revocation would invent a decision nobody made.
    """
    decision = RevisableDecision(
        requested=requested_capabilities(_manifest()),
        granted=frozenset(ALL) - {"tool.test"},
        denials=(),
        principal="op",
        policy="test",
    )
    bridge = _HostThatRevokes(
        FakeHostBridge(agents={"developer": PROPOSAL}), decision, after=99,
        capability="tool.test", reason="unused",
    )
    outcome = run_source(FIX_ISSUE, path="fix_issue.hx", bridge=bridge, inputs=INPUTS,
                        authority=decision)
    assert outcome.status == "denied"
    assert outcome.error.detail["revoked"] is False
    assert "did not grant" in outcome.error.message
    assert len(bridge.calls) == 4, "the run reached the crossing it could not pay for"


def test_the_revocation_is_logged_between_the_crossings_it_separated():
    outcome, _, _ = _revoking_run()
    kinds = [event["kind"] for event in outcome.events]
    assert "grant.revoked" in kinds
    revision = next(event for event in outcome.events if event["kind"] == "grant.revoked")
    assert revision["capability"] == "tool.test"
    assert revision["reason"] == "operator closed the test runner"
    finished = [index for index, kind in enumerate(kinds) if kind == "call.finished"]
    assert len(finished) == 4, "four crossings completed before the withdrawal"
    assert kinds.index("grant.revoked") > finished[-1]


def test_restoring_a_revoked_capability_lets_the_run_finish_and_keeps_the_history():
    decision = as_revisable(_decision())
    inner = FakeHostBridge(agents={"developer": PROPOSAL})
    seen: list[str] = []

    class _FreezeThenLift:
        """One crossing is refused authority, then the host changes its mind."""

        def __init__(self) -> None:
            self.frozen = False

        def read_file(self, path):
            return self._delegate(inner.read_file, "repo.read", path)

        def list_files(self, prefix):
            return self._delegate(inner.list_files, "repo.list", prefix)

        def invoke_agent(self, agent, operation, payload):
            return self._delegate(inner.invoke_agent, "model.invoke", agent, operation, payload)

        def propose_patch(self, changes):
            out = inner.propose_patch(changes)
            seen.append("repo.propose_patch")
            # The operator's second thought lands after this crossing, before the next.
            decision.revoke("tool.test", reason="freeze")
            decision.restore("tool.test", reason="freeze lifted")
            return out

        def run_tests(self, target, suite):
            return self._delegate(inner.run_tests, "verify.tests", target, suite)

        def _delegate(self, call, name, *args):
            seen.append(name)
            return call(*args)

    outcome = run_source(
        FIX_ISSUE,
        path="fix_issue.hx",
        bridge=_FreezeThenLift(),
        inputs=INPUTS,
        authority=decision,
    )
    assert outcome.status == "ok", outcome.error
    assert [revision.action for revision in decision.revisions()] == ["revoked", "restored"]
    assert "verify.tests" in seen, "the restored capability was usable again"


def test_a_revocation_cannot_widen_the_request():
    """Narrowing is the whole point.

    Widening would let a run grow its own ceiling by waiting, so ``restore`` only
    ever returns a capability this program actually asked for. ``cost_ceiling.hx``
    requires ``repo.read`` and nothing else, which makes the refusal observable.
    """
    single = (FIXTURES / "valid" / "cost_ceiling.hx").read_text(encoding="utf-8")
    decision = as_revisable(_decision(single, granted=("repo.read",)))
    assert decision.revoke("repo.read", reason="yes") is True
    assert decision.allows("repo.read") is False
    assert decision.restore("tool.test", reason="too late") is False
    assert decision.restore("repo.read", reason="changed my mind") is True
    assert decision.allows("repo.read") is True
    assert [revision.action for revision in decision.revisions()] == [
        "revoked",
        "restored",
    ]


def test_a_revoked_run_then_restored_authority_finishes_the_next_run():
    """The story a reviewer needs: revocation stops *this* run, not the program."""
    single = (FIXTURES / "valid" / "cost_ceiling.hx").read_text(encoding="utf-8")
    decision = as_revisable(_decision(single, granted=("repo.read",)))
    decision.revoke("repo.read", reason="budget freeze")
    stopped = run_source(single, bridge=FakeHostBridge(), inputs={}, authority=decision)
    assert stopped.status == "denied"
    assert stopped.error.detail["revoked"] is True
    decision.restore("repo.read", reason="freeze lifted")
    allowed = run_source(single, bridge=FakeHostBridge(), inputs={}, authority=decision)
    assert allowed.status == "ok", allowed.error
    assert [revision["action"] for revision in allowed.evidence()["policy"]["revisions"]] == [
        "revoked",
        "restored",
    ]


def test_the_evidence_records_the_starting_grant_and_the_live_one():
    outcome, _, _ = _revoking_run()
    policy = outcome.evidence()["policy"]
    assert policy["live"] is True
    assert sorted(policy["initiallyGranted"]) == sorted(ALL)
    assert "tool.test" not in policy["granted"]
    assert [revision["action"] for revision in policy["revisions"]] == ["revoked"]


def test_authority_minted_for_a_different_program_is_refused_not_borrowed():
    smaller = """
module smaller
schema RepoFile {
  path: String
  text: String
}
workflow one() -> RepoFile {
  requires repo.read
  budget { steps: 5; duration: 1m }
  return await repo.read("README.md")
}
"""
    outcome = run_source(
        smaller, bridge=FakeHostBridge(), inputs={}, authority=as_revisable(_decision())
    )
    assert outcome.status == "denied"
    assert outcome.error.code == "HXH404"
    assert "different request" in str(outcome.error.detail["problems"])


def test_a_supplied_authority_cannot_carry_a_human_only_permission():
    """The invariant is checked on the hand-in path, not only inside ``authorize``.

    No capability in today's table maps to a human-only permission — that is the
    point of the table — so the test injects the bug it is guarding against and
    shows the guard catches it.
    """
    from harness_hx.capabilities import CAPABILITIES, Capability

    decision = _decision()
    forged = RevisableDecision(
        requested=decision.requested,
        granted=frozenset(set(decision.granted) | {"repo.approve"}),
        denials=decision.denials,
        principal="op",
        policy="test",
    )
    CAPABILITIES["repo.approve"] = Capability("repo.approve", "approve", "an injected bug")
    try:
        problems = authority_problems(forged, _manifest())
        assert any("human-only" in problem for problem in problems), problems
        outcome = run_source(FIX_ISSUE, bridge=FakeHostBridge(), inputs=INPUTS,
                             authority=forged)
        assert outcome.status == "denied"
        assert outcome.error.code == "HXH404"
    finally:
        CAPABILITIES.pop("repo.approve")


def test_a_plain_decision_handed_in_behaves_as_it_always_did():
    outcome = run_source(
        FIX_ISSUE,
        path="fix_issue.hx",
        bridge=FakeHostBridge(agents={"developer": PROPOSAL}),
        inputs=INPUTS,
        authority=_decision(),
    )
    assert outcome.status == "ok", outcome.error
    policy = outcome.evidence()["policy"]
    assert "live" not in policy, "a frozen Decision does not claim to be revisable"


def test_revocation_and_pricing_compose_in_one_run():
    """A priced run that loses authority stops on authority, and reports the spend.

    The two M6 mechanisms are independent, but a reviewer sees them together: the
    crossings that ran were charged, the one that did not was denied before its
    price was ever reserved.
    """
    prices = dict.fromkeys(
        ("repo.read", "developer.plan", "developer.propose", "repo.propose_patch",
         "verify.tests"),
        "0.10",
    )
    outcome, bridge, _ = _revoking_run(prices=prices)
    assert outcome.status == "denied"
    assert outcome.error.code == "HXH401"
    assert len(bridge.calls) == 4
    assert outcome.budget["cost"]["used"] == "0.4000", "four charged crossings, not five"
    assert Pricing.from_mapping(prices).price_for("verify.tests") == Pricing.from_mapping(
        prices
    ).price_for("repo.read")
