"""OPS-20 Day 0 — the entrypoint must run a service's own `command:`.

`sandbox-egress` is a sidecar: it sets `command: python -m
harness_sandbox.egress_proxy` and has no `HARNESS_SERVICE`. The entrypoint
never read its arguments, so that container ignored its own command and fell
through to the all-in-one branch — seven uvicorns plus a migrate job inside a
128MB sidecar. It crash-looped on the master-key gate and the sandbox proxy was
never running; 708 failed healthchecks went unnoticed because the demo replay
path does not need it.

These tests execute the real script. They need no database, because the exec
happens before any app-tier work — which is the property being pinned.
"""

import os
import subprocess
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[3] / "deploy" / "entrypoint.sh"


def _run(args: list[str], extra_env: dict[str, str] | None = None):
    env = dict(os.environ)
    env.pop("HARNESS_SERVICE", None)
    env.pop("HARNESS_MASTER_KEY", None)
    env.pop("HARNESS_ALLOW_DEV_MASTER_KEY", None)
    env.update(extra_env or {})
    return subprocess.run(
        ["/bin/sh", str(ENTRYPOINT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_a_command_runs_instead_of_the_app_tier():
    result = _run(["sh", "-c", "echo COMMAND-RAN"])
    assert result.returncode == 0, result.stderr
    assert "COMMAND-RAN" in result.stdout
    # The all-in-one branch would have hit the master-key gate first; reaching
    # it at all means the command was ignored.
    assert "Refusing to start" not in result.stderr


def test_the_command_receives_its_own_argv():
    """No shell re-splitting: argv elements stay whole (the compose form)."""
    result = _run(["python3", "-c", "import sys; print(sys.argv[1:])", "a b", "--flag"])
    assert result.returncode == 0, result.stderr
    assert "['a b', '--flag']" in result.stdout


def test_a_failing_command_propagates_its_exit_code():
    result = _run(["sh", "-c", "exit 42"])
    assert result.returncode == 42


def test_a_command_runs_even_when_harness_service_is_set():
    """A sidecar must not be silently converted into an app tier by stray env."""
    result = _run(
        ["sh", "-c", "echo COMMAND-RAN"], {"HARNESS_SERVICE": "sandbox-egress"}
    )
    assert result.returncode == 0, result.stderr
    assert "COMMAND-RAN" in result.stdout
    assert "unknown HARNESS_SERVICE" not in result.stderr


def test_without_a_command_the_service_branch_still_guards_unknown_services():
    result = _run([], {"HARNESS_SERVICE": "sandbox-egress"})
    assert result.returncode == 1
    assert "unknown HARNESS_SERVICE" in result.stderr
