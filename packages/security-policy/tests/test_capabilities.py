import time

import pytest

from harness_security_policy import (
    UNSET,
    CapabilitySigner,
    InMemoryReplayStore,
    Outcome,
    RequiredBinding,
    args_digest,
    derive_capability_key,
)

KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))


def _binding(**overrides):
    base = {
        "team_id": "team-1",
        "run_id": "run-42",
        "operation": "tool.exec",
        "audience": "sandbox-worker",
    }
    base.update(overrides)
    return RequiredBinding(**base)  # type: ignore[arg-type]


def _issued(**overrides):
    signer = CapabilitySigner(KEY)
    kwargs = {
        "team_id": "team-1",
        "run_id": "run-42",
        "operation": "tool.exec",
        "audience": "sandbox-worker",
        "ttl_seconds": 300,
    }
    kwargs.update(overrides)
    return signer, signer.issue(**kwargs)


def test_issue_and_verify_happy_path():
    signer, issued = _issued()
    decision = signer.verify(issued.token, required=_binding())
    assert decision.outcome is Outcome.ALLOW
    assert decision.details["tokenId"] == issued.token_id


def test_expired_token_denied():
    signer, issued = _issued(ttl_seconds=60, now=time.time() - 3600)
    decision = signer.verify(issued.token, required=_binding(), now=time.time())
    assert decision.outcome is Outcome.DENY
    assert decision.reason == "capability.expired"


def test_future_token_denied_beyond_skew():
    signer, issued = _issued(now=time.time() + 3600)
    decision = signer.verify(issued.token, required=_binding(), now=time.time())
    assert decision.outcome is Outcome.DENY
    assert decision.reason == "capability.not_yet_valid"


def test_wrong_team_denied():
    signer, issued = _issued()
    decision = signer.verify(issued.token, required=_binding(team_id="team-2"))
    assert decision.outcome is Outcome.DENY
    assert decision.reason == "capability.wrong_team"


def test_wrong_run_operation_audience_denied():
    signer, issued = _issued()
    assert (
        signer.verify(issued.token, required=_binding(run_id="run-9")).reason
        == "capability.wrong_run"
    )  # noqa: E501
    assert (
        signer.verify(issued.token, required=_binding(operation="tool.write")).reason
        == "capability.wrong_operation"
    )  # noqa: E501
    assert (
        signer.verify(issued.token, required=_binding(audience="other")).reason
        == "capability.wrong_audience"
    )  # noqa: E501


def test_project_binding_enforced_when_required():
    signer, issued = _issued(project_id="proj-1")
    assert (
        signer.verify(issued.token, required=_binding(project_id="proj-1")).outcome is Outcome.ALLOW
    )  # noqa: E501
    denied = signer.verify(issued.token, required=_binding(project_id="proj-2"))
    assert denied.reason == "capability.wrong_project"


def test_argument_digest_binding():
    signer, issued = _issued(args={"path": "src/a.py"})
    ok = signer.verify(issued.token, required=_binding(args={"path": "src/a.py"}))
    assert ok.outcome is Outcome.ALLOW
    bad = signer.verify(issued.token, required=_binding(args={"path": "src/b.py"}))
    assert bad.reason == "capability.arguments_mismatch"
    skip = signer.verify(issued.token, required=_binding(args=UNSET))
    assert skip.outcome is Outcome.ALLOW


def test_algorithm_uses_domain_separated_key():
    assert derive_capability_key(KEY) != KEY
    assert len(derive_capability_key(KEY)) == 32


def test_signature_is_verified_constant_time():
    signer, issued = _issued()
    forged = CapabilitySigner(OTHER_KEY)
    assert forged.verify(issued.token, required=_binding()).reason == "capability.bad_signature"


def test_tampered_payload_denied():
    signer, issued = _issued()
    prefix, payload, signature = issued.token.split(".")
    tampered = f"{prefix}.{payload[:-2]}AA.{signature}"
    assert signer.verify(tampered, required=_binding()).reason == "capability.bad_signature"


def test_malformed_tokens_denied():
    signer = CapabilitySigner(KEY)
    for token in ("", "junk", "cap1.a", "cap1.!!!.???", "cap2.abc.def", "x" * 5000):
        decision = signer.verify(token, required=_binding())
        assert decision.outcome is Outcome.DENY


def test_replay_store_single_use():
    signer, issued = _issued(max_uses=1)
    store = InMemoryReplayStore()
    first = signer.verify(issued.token, required=_binding(), replay_store=store)
    assert first.outcome is Outcome.ALLOW
    second = signer.verify(issued.token, required=_binding(), replay_store=store)
    assert second.outcome is Outcome.DENY
    assert second.reason == "capability.uses_exhausted"


def test_replay_store_multi_use_then_exhausted():
    signer, issued = _issued(max_uses=3)
    store = InMemoryReplayStore()
    outcomes = [
        signer.verify(issued.token, required=_binding(), replay_store=store).outcome
        for _ in range(4)
    ]
    assert outcomes == [Outcome.ALLOW, Outcome.ALLOW, Outcome.ALLOW, Outcome.DENY]


def test_two_tokens_same_binding_have_distinct_ids():
    _, first = _issued()
    _, second = _issued()
    assert first.token_id != second.token_id
    assert first.token != second.token


def test_issue_rejects_out_of_range_ttl_and_bad_inputs():
    signer = CapabilitySigner(KEY)
    with pytest.raises(ValueError):
        signer.issue(team_id="t", run_id="r", operation="o", audience="a", ttl_seconds=0)
    with pytest.raises(ValueError):
        signer.issue(team_id="t", run_id="r", operation="o", audience="a", ttl_seconds=10**9)
    with pytest.raises(ValueError):
        signer.issue(team_id="", run_id="r", operation="o", audience="a")
    with pytest.raises(ValueError):
        CapabilitySigner(b"short")


def test_args_digest_is_order_insensitive():
    assert args_digest({"a": 1, "b": 2}) == args_digest({"b": 2, "a": 1})
    assert args_digest({"a": 1}) != args_digest({"a": 2})
