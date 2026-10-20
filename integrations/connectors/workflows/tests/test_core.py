"""Core primitives: signatures, idempotency claim/settle/release, redaction."""

from __future__ import annotations

import time

import pytest

from harness_workflow_connectors import (
    IdempotencyStore,
    IntakeRejected,
    RateLimiter,
    SignatureScheme,
    TenantConfig,
    TenantMap,
    redact_secrets,
    sign,
    verify_signature,
)

SCHEME = SignatureScheme(
    name="test",
    header="X-Sig",
    timestamp_header="X-Ts",
    prefix="v0=",
    timestamped_body="v0:{ts}:{body}",
    tolerance_s=300,
)


def test_signature_accepts_valid_and_rejects_tamper():
    ts = str(int(time.time()))
    body = b'{"a": 1}'
    header = sign(SCHEME, "s3cret", body, ts)
    verify_signature(SCHEME, "s3cret", body, {"X-Sig": header, "X-Ts": ts})

    with pytest.raises(IntakeRejected) as err:
        verify_signature(SCHEME, "s3cret", b'{"a": 2}', {"X-Sig": header, "X-Ts": ts})
    assert err.value.code == "SIGNATURE_INVALID"

    with pytest.raises(IntakeRejected) as err2:
        verify_signature(SCHEME, "wrong-secret", body, {"X-Sig": header, "X-Ts": ts})
    assert err2.value.code == "SIGNATURE_INVALID"


def test_signature_replay_window_and_missing_headers():
    old_ts = str(int(time.time()) - 3600)
    body = b"{}"
    header = sign(SCHEME, "s3cret", body, old_ts)
    with pytest.raises(IntakeRejected) as err:
        verify_signature(SCHEME, "s3cret", body, {"X-Sig": header, "X-Ts": old_ts})
    assert err.value.code == "SIGNATURE_STALE"

    with pytest.raises(IntakeRejected) as err2:
        verify_signature(SCHEME, "s3cret", body, {"X-Ts": str(int(time.time()))})
    assert err2.value.code == "SIGNATURE_MISSING"


def test_idempotency_claim_settle_release(tmp_path):
    store = IdempotencyStore(str(tmp_path / "d.sqlite"))
    assert store.claim("slack", "evt-1") is True
    assert store.claim("slack", "evt-1") is False  # concurrent duplicate cannot proceed
    store.settle("slack", "evt-1", "plan_proposed", "plan-9")
    assert store.get_outcome("slack", "evt-1") == "plan_proposed"

    assert store.claim("slack", "evt-2") is True
    store.release("slack", "evt-2")  # simulated upstream failure
    assert store.claim("slack", "evt-2") is True  # provider retry re-enters
    store.close()


def test_redaction_catches_common_secret_shapes():
    text = (
        "Authorization: Bearer abc123def456ghi789\n"
        "token xoxb-1234567890-abcdefghij\n"
        "AWS AKIAIOSFODNN7EXAMPLE\n"
        "password = hunter2hunter2\n"
        "api_key: sk_live_abcdef1234567890\n"
    )
    out = redact_secrets(text)
    for leak in ("abc123def456ghi789", "xoxb-", "AKIAIOSFODNN7EXAMPLE",
                 "hunter2hunter2", "sk_live_abcdef1234567890"):
        assert leak not in out
    assert out.count("[REDACTED]") >= 5
    assert "Authorization: Bearer [REDACTED]" in out


def test_rate_limiter_window():
    limiter = RateLimiter()
    now = time.time()
    for i in range(3):
        limiter.check(("slack", "T1"), 3, now=now + i)
    with pytest.raises(IntakeRejected) as err:
        limiter.check(("slack", "T1"), 3, now=now + 3)
    assert err.value.code == "RATE_LIMITED"
    limiter.check(("slack", "T1"), 3, now=now + 61)  # window slides


def test_tenant_map_fails_closed():
    tenants = TenantMap(
        tenants={
            "slack": {
                "T1": TenantConfig(team_id="team-a"),
                "T2": TenantConfig(team_id="team-b", enabled=False),
            }
        }
    )
    assert tenants.resolve("slack", "T1").team_id == "team-a"
    for bad in ("T2", "T-unknown", None, ""):
        with pytest.raises(IntakeRejected) as err:
            tenants.resolve("slack", bad)
        assert (err.value.code, err.value.status_code) == ("TENANT_UNKNOWN", 403)
