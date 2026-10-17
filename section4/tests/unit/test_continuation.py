"""A2-S3: handoff (F025), follow-up (F026), retry (F028), recovery (F029)."""

import asyncio

import pytest

from harness_orchestrator.continuation import (
    AttemptLedger,
    CheckpointStore,
    FollowupError,
    FollowupInbox,
    HandoffError,
    HandoffSigner,
    RetryPolicy,
    choose_strategy,
    classify,
    render_followups,
    run_with_retry,
)

KEY = b"k" * 32
SCOPE = dict(team_id="t1", run_id="r1", target_env="cloud")


def _tok(signer, **kw):
    return signer.issue(
        team_id="t1",
        run_id="r1",
        source_env="local",
        target_env="cloud",
        checkpoint={"done": ["s1"]},
        **kw,
    )


def test_handoff_roundtrip_single_use():
    s = HandoffSigner(KEY)
    t = _tok(s)
    assert s.accept(t, **SCOPE) == {"done": ["s1"]}
    with pytest.raises(HandoffError) as e:
        s.accept(t, **SCOPE)
    assert e.value.code == "handoff.replayed"


@pytest.mark.parametrize(
    "over,code",
    [
        ({"team_id": "t2"}, "handoff.wrong_scope"),
        ({"run_id": "r2"}, "handoff.wrong_scope"),
        ({"target_env": "local"}, "handoff.wrong_target"),
    ],
)
def test_handoff_scope_binding(over, code):
    s = HandoffSigner(KEY)
    with pytest.raises(HandoffError) as e:
        s.accept(_tok(s), **{**SCOPE, **over})
    assert e.value.code == code


def test_handoff_tamper_expiry_secret_and_other_key():
    now = [1000.0]
    s = HandoffSigner(KEY, clock=lambda: now[0])
    t = _tok(s, ttl_seconds=10)
    raw, mac = t.split(".")
    with pytest.raises(HandoffError) as e:
        s.accept(raw + "A." + mac, **SCOPE)
    assert e.value.code in {"handoff.bad_signature", "handoff.malformed"}
    with pytest.raises(HandoffError) as e:
        HandoffSigner(b"z" * 32).accept(t, **SCOPE)
    assert e.value.code == "handoff.bad_signature"
    now[0] += 11
    with pytest.raises(HandoffError) as e:
        s.accept(t, **SCOPE)
    assert e.value.code == "handoff.expired"
    for bad in ({"apiKey": "x"}, {"note": "sk-abcdefghijklmnopqrstuv"}):
        with pytest.raises(HandoffError) as e:
            s.issue(
                team_id="t1", run_id="r1", source_env="local", target_env="cloud", checkpoint=bad
            )
        assert e.value.code == "handoff.secret_in_manifest"
    with pytest.raises(HandoffError):
        s.issue(team_id="t", run_id="r", source_env="local", target_env="local", checkpoint={})


def test_followup_inbox_bounds_and_once_delivery():
    box = FollowupInbox()
    box.post("r1", "u1", "use pytest")
    box.post("r1", "u2", "skip docs")
    got = box.drain("r1")
    assert [g["author"] for g in got] == ["u1", "u2"] and box.drain("r1") == []
    assert render_followups(got).startswith("[follow-up from u1]")
    for bad in ("", "x" * 4001, "key sk-abcdefghijklmnopqrstuv"):
        with pytest.raises(FollowupError):
            box.post("r1", "u", bad)
    for _ in range(FollowupInbox.MAX_PENDING):
        box.post("r2", "u", "n")
    with pytest.raises(FollowupError, match="inbox_full"):
        box.post("r2", "u", "n")
    box.close("r1")
    with pytest.raises(FollowupError, match="not_active"):
        box.post("r1", "u", "late")


def _flaky(results):
    calls = []

    async def call():
        calls.append(1)
        return results[min(len(calls) - 1, len(results) - 1)]

    return call, calls


async def _nosleep(_):
    return None


def test_retry_transient_then_success_and_idempotent_replay():
    call, calls = _flaky(
        [
            {"status": "error", "finalResponse": "429 rate limit"},
            {"status": "completed", "finalResponse": "ok"},
        ]
    )
    ledger = AttemptLedger()
    r = asyncio.run(
        run_with_retry(call, key="k1", policy=RetryPolicy(), ledger=ledger, sleep=_nosleep)
    )
    assert r["status"] == "completed" and r["attempts"] == 2 and len(calls) == 2
    again = asyncio.run(
        run_with_retry(call, key="k1", policy=RetryPolicy(), ledger=ledger, sleep=_nosleep)
    )
    assert again == r and len(calls) == 2  # replay re-executes nothing


def test_retry_permanent_never_retries_and_bound_holds():
    call, calls = _flaky([{"status": "error", "engine": "mode_guard", "finalResponse": "x"}])
    asyncio.run(
        run_with_retry(call, key="p", policy=RetryPolicy(), ledger=AttemptLedger(), sleep=_nosleep)
    )
    assert len(calls) == 1
    call, calls = _flaky([{"status": "error", "finalResponse": "timeout"}])
    r = asyncio.run(
        run_with_retry(
            call,
            key="b",
            policy=RetryPolicy(max_attempts=3),
            ledger=AttemptLedger(),
            sleep=_nosleep,
        )
    )
    assert len(calls) == 3 and r["status"] == "error"


def test_backoff_is_capped_and_classify():
    p = RetryPolicy(base_delay=1, max_delay=4)
    assert [p.delay(i) for i in (1, 2, 3, 4)] == [1, 2, 4, 4]
    assert classify({"status": "completed"}) == "ok"
    assert classify({"status": "error", "finalResponse": "boom"}) == "permanent"


def test_recovery_strategy_is_bounded_and_never_switches_model():
    ch = lambda **k: choose_strategy(max_attempts=3, **k)  # noqa: E731
    assert ch(kind="transient", attempts=1, has_checkpoint=True) == "resume_checkpoint"
    assert ch(kind="transient", attempts=1, has_checkpoint=False) == "retry_same"
    assert ch(kind="transient", attempts=3, has_checkpoint=True) == "escalate_human"
    assert ch(kind="permanent", attempts=1, has_checkpoint=True) == "escalate_human"
    assert ch(kind="denied", attempts=1, has_checkpoint=True) == "abort"


def test_checkpoint_resume_skips_completed_steps(tmp_path):
    store = CheckpointStore(tmp_path)
    ran: list[str] = []

    def run(steps, fail_at=None):
        done = (store.load("r1") or {}).get("done", [])
        for s in steps:
            if s in done:
                continue
            if s == fail_at:
                raise RuntimeError("crash")
            ran.append(s)
            done.append(s)
            store.save("r1", {"done": done})

    with pytest.raises(RuntimeError):
        run(["a", "b", "c"], fail_at="c")
    run(["a", "b", "c"])  # "restart"
    assert ran == ["a", "b", "c"]  # a,b not re-run after the crash
    with pytest.raises(ValueError):
        store.load("../etc")
