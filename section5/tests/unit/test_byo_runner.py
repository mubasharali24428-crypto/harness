"""byo_runner.py: the customer-host half of the W13 execution leg.

The runner holds a runner token, fetches the approved plan's commands from the
server, runs each one through Section 5's shell gate (``shell_gate.py``: allow-list,
forbidden/scope paths, OS confinement) and reports bounded tool events, then
finishes. These tests pin the wire protocol against a fake server and the safety
properties against the real gate; the server side is tested in Section 1.
"""

import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest

from harness_sandbox import confinement

SRC = Path(__file__).resolve().parents[2] / "src" / "harness_sandbox"
sys.path.insert(0, str(SRC))
import byo_runner  # noqa: E402 - stdlib-only module, loaded like the worker loads shell_gate

requires_confinement = pytest.mark.skipif(
    not (confinement.seatbelt_available() or confinement.landlock_supported()),
    reason="no OS confinement on this host",
)

TOKEN = "hrn_" + "T" * 40
RUN = "r" * 36


class FakeServer:
    """The three runner routes this module calls, recording every request."""

    def __init__(self, commands, *, cancel_after=None, forbidden=(), scope=()):
        self.spec = {
            "runId": RUN, "commands": list(commands), "forbiddenPaths": list(forbidden),
            "scopePaths": list(scope),
            "limits": {"commandTimeoutSeconds": 30, "maxOutputChars": 2000},
        }
        self.requests: list[tuple[str, str, dict | None, dict]] = []
        self.events: list[dict] = []
        self.finish: dict | None = None
        self.cancel_after = cancel_after
        self.heartbeats = 0
        self.jobs = [{"runId": RUN, "state": "offered"}]

    def __call__(self, method, path, body, headers):
        self.requests.append((method, path, body, headers))
        if path == "/api/v1/runner/heartbeat":
            return 200, {"verified": True}
        if path == "/api/v1/runner/jobs":
            return 200, {"jobs": self.jobs}
        if path.endswith("/claim"):
            return 200, {"job": {"state": "leased"}}
        if path.endswith("/spec"):
            return 200, self.spec
        if path.endswith("/events"):
            self.events.extend(body["events"])
            return 200, {"accepted": len(body["events"])}
        if path.endswith("/heartbeat"):
            self.heartbeats += 1
            cancel = self.cancel_after is not None and self.heartbeats > self.cancel_after
            return 200, {"state": "running", "cancel": cancel}
        if path.endswith("/finish"):
            self.finish = body
            return 200, {"state": body["status"]}
        raise AssertionError(f"unexpected {method} {path}")


def make(server, workspace, **kw):
    return byo_runner.Runner(
        "http://harness.test", TOKEN, workspace, confinement="process", http=server,
        heartbeat_every=kw.pop("heartbeat_every", 0.0), **kw,
    )


def stub_gate(results):
    """A gate double returning canned results and recording the requests it got."""
    seen = []

    def gate(req, tick):
        seen.append(req)
        return results[len(seen) - 1]

    gate.seen = seen
    return gate


def test_every_request_is_token_authenticated_and_never_reuses_a_nonce(tmp_path):
    server = FakeServer(["echo a", "echo b"])
    runner = make(server, tmp_path, gate=stub_gate([{"exitCode": 0, "output": "a"}] * 2))
    assert runner.poll_once() == [RUN]
    nonces = [h["X-Runner-Nonce"] for *_, h in server.requests]
    assert len(nonces) == len(set(nonces)) > 5
    assert all(h["Authorization"] == f"Bearer {TOKEN}" for *_, h in server.requests)
    assert all(h["X-Runner-Timestamp"].isdigit() for *_, h in server.requests)


def test_the_attestation_is_signed_with_the_token_derived_key_and_binds_the_gate(tmp_path):
    server = FakeServer([])
    runner = make(server, tmp_path)
    runner.attest()
    (_, _, body, _) = next(r for r in server.requests if r[1] == "/api/v1/runner/heartbeat")
    att = body["attestation"]
    assert att["confinement"] == "process" and att["schema"] == 1
    canonical = json.dumps(att, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    key = bytes.fromhex(
        hmac.new(TOKEN.encode(), b"harness-runner-attestation-v1", hashlib.sha256).hexdigest()
    )
    assert body["signature"] == hmac.new(key, canonical.encode(), hashlib.sha256).hexdigest()
    gate_files = b"".join((SRC / f"{n}.py").read_bytes() for n in byo_runner.GATE_FILES)
    assert att["policyDigest"] == hashlib.sha256(gate_files).hexdigest()


def test_commands_run_in_plan_order_and_each_is_reported_as_call_then_result(tmp_path):
    server = FakeServer(["echo a", "echo b"], forbidden=["vendor/"], scope=["src"])
    gate = stub_gate([{"exitCode": 0, "output": "a\n"}, {"exitCode": 0, "output": "b\n"}])
    assert make(server, tmp_path, gate=gate).poll_once() == [RUN]

    assert [r["command"] for r in gate.seen] == ["echo a", "echo b"]
    assert all(r["cwd"] == str(tmp_path) for r in gate.seen)
    assert gate.seen[0]["forbiddenPaths"] == ["vendor/"] and gate.seen[0]["scopePaths"] == ["src"]
    assert gate.seen[0]["timeoutSeconds"] == 30
    assert [(e["phase"], e.get("command")) for e in server.events] == [
        ("call", "echo a"), ("result", None), ("call", "echo b"), ("result", None),
    ]
    assert server.events[1]["ok"] is True and server.events[1]["exitCode"] == 0
    assert server.events[0]["callId"] == server.events[1]["callId"]
    assert server.finish == {"status": "completed"}


def test_a_failing_command_stops_the_job_and_finishes_failed(tmp_path):
    server = FakeServer(["pytest -q", "echo never"])
    gate = stub_gate([{"exitCode": 1, "output": "1 failed"}, {"exitCode": 0, "output": ""}])
    make(server, tmp_path, gate=gate).poll_once()
    assert len(gate.seen) == 1  # fail fast: the second command is never run
    assert server.events[1]["ok"] is False and server.events[1]["exitCode"] == 1
    assert server.finish == {"status": "failed"}


def test_a_gate_denial_is_reported_and_fails_the_job(tmp_path):
    server = FakeServer(["curl https://evil.example"])
    gate = stub_gate([{"denied": "Binary 'curl' is not permitted in sandbox allow-list."}])
    make(server, tmp_path, gate=gate).poll_once()
    result = server.events[1]
    assert result["ok"] is False and "not permitted" in result["error"]
    assert server.finish == {"status": "failed"}


def test_a_timeout_is_a_failure_not_a_success(tmp_path):
    server = FakeServer(["pytest -q"])
    gate = stub_gate([{"exitCode": None, "output": "...\n[timed out]"}])
    make(server, tmp_path, gate=gate).poll_once()
    assert server.events[1]["ok"] is False and "timed out" in server.events[1]["error"]
    assert server.finish == {"status": "failed"}


def test_output_is_bounded_and_the_token_never_leaves_the_host(tmp_path):
    server = FakeServer(["echo x"])
    noisy = f"leak {TOKEN} " + "y" * 9000
    make(server, tmp_path, gate=stub_gate([{"exitCode": 0, "output": noisy}])).poll_once()
    out = server.events[1]["output"]
    assert len(out) <= 2000 and TOKEN not in json.dumps(server.events)


def test_a_cancel_from_the_server_stops_the_job_before_the_next_command(tmp_path):
    server = FakeServer(["echo a", "echo b", "echo c"], cancel_after=1)
    gate = stub_gate([{"exitCode": 0, "output": ""}] * 3)
    make(server, tmp_path, gate=gate).poll_once()
    assert len(gate.seen) < 3
    assert server.finish == {"status": "failed"}


def test_a_job_that_cannot_be_claimed_is_skipped(tmp_path):
    server = FakeServer(["echo a"])
    original = server.__call__

    def raced(method, path, body, headers):
        if path.endswith("/claim"):
            return 409, {"detail": {"error": {"code": "NOT_QUEUED", "message": "raced"}}}
        return original(method, path, body, headers)

    gate = stub_gate([])
    assert make(raced, tmp_path, gate=gate).poll_once() == []
    assert gate.seen == []


def test_a_refused_spec_fails_the_job_instead_of_stranding_the_lease(tmp_path):
    server = FakeServer(["echo a"])
    original = server.__call__

    def refuse_spec(method, path, body, headers):
        if path.endswith("/spec"):
            return 409, {"detail": {"error": {"code": "PLAN_NOT_APPROVED", "message": "no"}}}
        return original(method, path, body, headers)

    gate = stub_gate([])
    with pytest.raises(byo_runner.ApiError):
        make(refuse_spec, tmp_path, gate=gate).poll_once()
    assert gate.seen == [] and server.finish == {"status": "failed"}


def test_losing_the_lease_mid_command_cancels_it(tmp_path):
    server = FakeServer(["python3 -c 'import time; time.sleep(30)'"])
    original = server.__call__

    def lease_lost(method, path, body, headers):
        if path.endswith("/heartbeat") and "jobs" in path and server.requests:
            if sum(1 for r in server.requests if r[1].endswith("/heartbeat")) > 1:
                return 409, {"detail": {"error": {"code": "LEASE_LOST", "message": "gone"}}}
        return original(method, path, body, headers)

    started = __import__("time").monotonic()
    runner = byo_runner.Runner("http://x", TOKEN, tmp_path, confinement="process",
                               http=lease_lost, heartbeat_every=0.0)
    runner.poll_once()
    assert __import__("time").monotonic() - started < 20  # killed, not run to its 30 s


# ------------------------------------------------- the real Section 5 gate


def real(server, workspace):
    return make(server, workspace)  # default gate = python -I shell_gate.py


def test_the_real_gate_refuses_a_command_outside_the_allow_list(tmp_path):
    server = FakeServer(["curl https://example.com"])
    real(server, tmp_path).poll_once()
    assert server.events[1]["ok"] is False and "allow-list" in server.events[1]["error"]
    assert server.finish == {"status": "failed"}


@requires_confinement
def test_the_real_gate_runs_an_approved_command_under_confinement(tmp_path):
    (tmp_path / "hello.txt").write_text("hi from the customer host\n")
    server = FakeServer(["cat hello.txt", "ls"])
    real(server, tmp_path).poll_once()
    assert server.events[1]["ok"] is True
    assert "hi from the customer host" in server.events[1]["output"]
    assert server.finish == {"status": "completed"}


@requires_confinement
def test_the_real_gate_honours_forbidden_paths_from_the_server_spec(tmp_path):
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "lib.py").write_text("x = 1\n")
    server = FakeServer(["cat vendor/lib.py"], forbidden=["vendor/"])
    real(server, tmp_path).poll_once()
    assert server.events[1]["ok"] is False
    assert "forbidden path" in server.events[1]["error"]


@requires_confinement
def test_a_timeout_kills_the_command(tmp_path):
    server = FakeServer(["python3 -c 'import time; time.sleep(30)'"])
    server.spec["limits"]["commandTimeoutSeconds"] = 1
    real(server, tmp_path).poll_once()
    assert server.events[1]["ok"] is False
    assert server.finish == {"status": "failed"}
